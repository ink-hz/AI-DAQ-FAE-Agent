"""Real local children and filesystem; only required root UID is substituted."""
import importlib.util
import json
import os
import signal
import subprocess
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[2] / 'deploy/security_action.py'


class ActionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2])
        self.root = Path(self.tmp.name)
        os.chmod(self.root, 0o700)
        spec = importlib.util.spec_from_file_location('security_action', MODULE)
        self.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.m)
        self.uid = patch.object(self.m, 'ROOT_UID', os.geteuid())
        self.uid.start()
        self.attempt = 'a' * 32
        self.intent = {'version': 1, 'run_attempt': self.attempt, 'operation': 'source-stop',
                       'role': 'source', 'kind': 'stop', 'argv': [os.path.realpath(sys.executable), '-c', 'pass'],
                       'expected': {'target_cid': 'b' * 64}}

    def tearDown(self):
        self.uid.stop()
        self.tmp.cleanup()

    def submit(self):
        p = self.root / 'input.json'
        p.write_text(json.dumps(self.intent))
        p.chmod(0o600)
        return self.m.submit(str(self.root), str(p), self.attempt, self.intent['operation'])

    def status(self):
        return self.m.status(str(self.root), self.attempt, self.intent['operation'])

    def finish(self, pid):
        os.waitpid(pid, 0)
        return self.status()

    def test_delayed_child_is_unknown_not_absent_then_ack(self):
        self.intent['argv'][-1] = 'import time; time.sleep(.15)'
        self.assertEqual('ABSENT', self.status()['state'])
        pid = self.submit()
        self.assertEqual('UNKNOWN', self.status()['state'])
        self.assertEqual('ACK', self.finish(pid)['state'])

    def test_success_is_cli_terminal_even_if_application_exits(self):
        self.intent['argv'][-1] = 'import subprocess,sys; subprocess.run([sys.executable,"-c","pass"],check=True)'
        result = self.finish(self.submit())
        self.assertEqual(0, result['ack']['returncode'])
        self.assertEqual('ACK', result['state'])

    def test_missing_and_partial_records_are_unknown_and_never_reused(self):
        op = self.root / self.intent['operation']
        op.mkdir(mode=0o700)
        for value in (None, '{'):
            if value:
                (op / 'intent.json').write_text(value)
                (op / 'intent.json').chmod(0o600)
            self.assertEqual('UNKNOWN', self.status()['state'])
            with self.assertRaises(self.m.Rejected):
                self.submit()

    def test_wrong_binding_cannot_adopt_ack(self):
        self.finish(self.submit())
        path = self.root / self.intent['operation'] / 'ack.json'
        original = json.loads(path.read_text())
        for key, value in [('run_attempt', 'c' * 32), ('argv_sha256', '0' * 64)]:
            record = dict(original, **{key: value})
            path.write_text(json.dumps(record))
            self.assertEqual('UNKNOWN', self.status()['state'])
        self.assertEqual('UNKNOWN', self.m.status(str(self.root), 'c' * 32, self.intent['operation'])['state'])

    def test_duplicate_launch_only_one_real_child(self):
        counter = self.root / 'counter'
        self.intent['argv'][-1] = f'from pathlib import Path; import time; Path({str(counter)!r}).write_text("one"); time.sleep(.1)'
        pid = self.submit()
        with self.assertRaises(self.m.Rejected):
            self.submit()
        result = self.finish(pid)
        self.assertEqual('one', counter.read_text())
        self.assertEqual('ACK', result['state'])
        self.assertEqual(1, len(list((self.root / 'source-stop').glob('claim.json'))))

    def test_output_is_continuously_drained_hashes_and_counts_only(self):
        self.intent['argv'][-1] = 'import os; os.write(1,b"private-body"*100000); os.write(2,b"err"*100000)'
        result = self.finish(self.submit())
        self.assertEqual(1200000, result['ack']['stdout']['bytes'])
        self.assertEqual(300000, result['ack']['stderr']['bytes'])
        self.assertNotIn('private-body', json.dumps(result))

    def test_strict_input_and_path_boundaries(self):
        for key, value in [('operation', '../bad'), ('kind', 'retry'), ('version', True), ('extra', 1), ('argv', 'shell')]:
            saved = self.intent.copy()
            self.intent[key] = value
            with self.assertRaises(self.m.Rejected):
                self.submit()
            self.intent = saved
        p = self.root / 'input.json'
        p.write_text(json.dumps(self.intent)); p.chmod(0o600)
        link = self.root / 'link.json'; link.symlink_to(p)
        with self.assertRaises(self.m.Rejected):
            self.m.submit(str(self.root), str(link), self.attempt, 'source-stop')
        link.unlink(); os.link(p, link)
        with self.assertRaises(self.m.Rejected):
            self.m.submit(str(self.root), str(p), self.attempt, 'source-stop')
        link.unlink(); self.root.chmod(0o770)
        with self.assertRaises(self.m.Rejected):
            self.m.submit(str(self.root), str(p), self.attempt, 'source-stop')

    def test_nonroot_and_duplicate_json_rejected(self):
        with patch.object(self.m, 'ROOT_UID', os.geteuid() + 1):
            with self.assertRaises(self.m.Rejected):
                self.status()
        p = self.root / 'input.json'
        p.write_text('{"version":1,"version":1}'); p.chmod(0o600)
        with self.assertRaises(self.m.Rejected):
            self.m.submit(str(self.root), str(p), self.attempt, 'source-stop')

    def wait_for(self, predicate):
        until = time.monotonic() + 3
        while time.monotonic() < until:
            if predicate():
                return
            time.sleep(.01)
        self.fail('local subprocess did not reach expected boundary')

    def test_continuous_durability_failure_before_launch_never_runs(self):
        counter = self.root / 'counter'
        self.intent['argv'][-1] = f'from pathlib import Path; Path({str(counter)!r}).touch()'
        for fail_at in range(1, 8):
            self.intent['operation'] = f'stop-{fail_at}'
            original = self.m.sync_dir
            calls = [0]
            def failure(path):
                calls[0] += 1
                if calls[0] >= fail_at:
                    raise OSError('injected persistent directory fsync failure')
                return original(path)
            with patch.object(self.m, 'sync_dir', failure):
                with self.assertRaises(OSError):
                    self.submit()
            self.assertFalse(counter.exists())
            self.assertEqual('UNKNOWN', self.status()['state'])
            with self.assertRaises(self.m.Rejected):
                self.submit()

    def test_continuous_ack_directory_fsync_failure_is_unknown(self):
        counter = self.root / 'counter'
        self.intent['argv'][-1] = f'from pathlib import Path; Path({str(counter)!r}).touch()'
        original = self.m.publish
        def failure(directory, name, value):
            if name == 'ack.json':
                self.m.sync_dir = lambda _: (_ for _ in ()).throw(OSError('continuous fsync failure'))
            return original(directory, name, value)
        with patch.object(self.m, 'publish', failure):
            pid = self.submit()
        result = self.finish(pid)
        self.assertTrue(counter.exists())
        self.assertTrue((self.root / 'source-stop' / 'ack.json').exists())
        self.assertEqual('UNKNOWN', result['state'])

    def test_continuous_file_fsync_failure_before_launch_blocks_operation(self):
        with patch.object(self.m.os, 'fsync', side_effect=OSError('continuous file fsync failure')):
            with self.assertRaises(OSError):
                self.submit()
        self.assertEqual('UNKNOWN', self.status()['state'])

    def test_claim_save_failure_never_launches_child(self):
        counter = self.root / 'counter'
        self.intent['argv'][-1] = f'from pathlib import Path; Path({str(counter)!r}).touch()'
        original = self.m.publish
        def failure(directory, name, value):
            if name == 'claim.json':
                raise OSError('continuous claim save failure')
            return original(directory, name, value)
        with patch.object(self.m, 'publish', failure):
            pid = self.submit()
        self.assertEqual('UNKNOWN', self.finish(pid)['state'])
        self.assertFalse(counter.exists())

    def test_supervisor_signal_waits_child_and_leaves_unknown(self):
        counter = self.root / 'counter'
        started = self.root / 'started'
        self.intent['argv'][-1] = f'import time; from pathlib import Path; Path({str(started)!r}).touch(); time.sleep(.2); Path({str(counter)!r}).touch()'
        pid = self.submit()
        self.wait_for(started.exists)
        os.kill(pid, signal.SIGTERM)
        self.assertEqual('UNKNOWN', self.finish(pid)['state'])
        self.assertTrue(counter.exists())
        claim = json.loads((self.root / 'source-stop' / 'claim.json').read_text())
        self.assertEqual(pid, claim['supervisor']['pid'])

    def test_disconnected_caller_does_not_cancel_delayed_child(self):
        counter = self.root / 'counter'
        self.intent['argv'][-1] = f'import time; from pathlib import Path; time.sleep(.2); Path({str(counter)!r}).write_text("one")'
        caller = os.fork()
        if caller == 0:
            try:
                self.submit()
            finally:
                os._exit(0)
        os.waitpid(caller, 0)
        self.assertEqual('UNKNOWN', self.status()['state'])
        self.wait_for(lambda: self.status()['state'] == 'ACK')
        self.assertEqual('one', counter.read_text())
        self.assertEqual(1, len(list((self.root / 'source-stop').glob('claim.json'))))

    def test_actual_concurrent_duplicate_operations_only_one_child(self):
        counter = self.root / 'counter'
        self.intent['argv'][-1] = f'from pathlib import Path; Path({str(counter)!r}).open("a").write("x")'
        p = self.root / 'input.json'
        p.write_text(json.dumps(self.intent)); p.chmod(0o600)
        callers = []
        for _ in range(2):
            caller = os.fork()
            if caller == 0:
                try:
                    supervisor = self.m.submit(str(self.root), str(p), self.attempt, 'source-stop')
                    os.waitpid(supervisor, 0)
                    os._exit(0)
                except self.m.Rejected:
                    os._exit(2)
                except BaseException:
                    os._exit(3)
            callers.append(caller)
        exits = sorted(os.waitpid(pid, 0)[1] >> 8 for pid in callers)
        self.assertEqual([0, 2], exits)
        self.assertEqual('x', counter.read_text())
        self.assertEqual('ACK', self.status()['state'])

    def test_supervisor_death_leaves_unknown_and_does_not_cancel_child(self):
        started = self.root / 'started'
        counter = self.root / 'counter'
        self.intent['argv'][-1] = f'import time; from pathlib import Path; Path({str(started)!r}).touch(); time.sleep(.2); Path({str(counter)!r}).touch()'
        pid = self.submit()
        self.wait_for(started.exists)
        os.kill(pid, signal.SIGKILL)
        self.assertEqual('UNKNOWN', self.finish(pid)['state'])
        self.wait_for(counter.exists)
        self.assertEqual('UNKNOWN', self.status()['state'])

    def test_continuous_ack_file_fsync_failure_after_child_is_unknown(self):
        counter = self.root / 'counter'
        self.intent['argv'][-1] = f'from pathlib import Path; Path({str(counter)!r}).touch()'
        original = self.m.publish
        def failure(directory, name, value):
            if name == 'ack.json':
                self.m.os.fsync = lambda _: (_ for _ in ()).throw(OSError('continuous file fsync failure'))
            return original(directory, name, value)
        with patch.object(self.m, 'publish', failure):
            pid = self.submit()
        self.assertEqual('UNKNOWN', self.finish(pid)['state'])
        self.assertTrue(counter.exists())

    def test_symlink_run_directory_and_writable_ancestor_are_rejected(self):
        alias = self.root / 'alias'
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(self.m.Rejected):
            self.m.status(str(alias), self.attempt, 'source-stop')
        nested = self.root / 'nested'; nested.mkdir(mode=0o700)
        self.root.chmod(0o777)
        with self.assertRaises(self.m.Rejected):
            self.m.status(str(nested), self.attempt, 'source-stop')
        self.root.chmod(0o700)

    def test_cli_bounded_wait_exits_unknown_then_real_child_finishes_once(self):
        counter = self.root / 'counter'
        self.intent['argv'][-1] = f'import time; from pathlib import Path; time.sleep(.2); Path({str(counter)!r}).write_text("one")'
        p = self.root / 'input.json'
        p.write_text(json.dumps(self.intent)); p.chmod(0o600)
        wrapper = ('import importlib.util,os,sys; '
                   f's=importlib.util.spec_from_file_location("runner",{str(MODULE)!r}); '
                   'm=importlib.util.module_from_spec(s); s.loader.exec_module(m); '
                   'm.ROOT_UID=os.geteuid(); sys.exit(m.main())')
        cli = subprocess.run([sys.executable, '-c', wrapper, 'submit', '--run-dir', str(self.root),
                              '--intent', str(p), '--attempt', self.attempt, '--operation', 'source-stop',
                              '--wait', '.02'], capture_output=True, text=True, timeout=3)
        self.assertEqual(2, cli.returncode)
        self.assertEqual('UNKNOWN', json.loads(cli.stdout)['state'])
        self.wait_for(lambda: self.status()['state'] == 'ACK')
        self.assertEqual('one', counter.read_text())

    def test_nonzero_cli_terminal_is_ack_with_actual_exit_code(self):
        self.intent['argv'][-1] = 'import sys; sys.exit(7)'
        result = self.finish(self.submit())
        self.assertEqual('ACK', result['state'])
        self.assertEqual(7, result['ack']['returncode'])

    def test_continuous_mkdir_failure_never_allocates_or_starts_child(self):
        with patch.object(Path, 'mkdir', side_effect=OSError('continuous mkdir failure')):
            with self.assertRaises(OSError):
                self.submit()
        self.assertEqual('ABSENT', self.status()['state'])

    def test_durable_claim_or_runroot_save_failure_never_starts_child(self):
        counter = self.root / 'counter'
        self.intent['argv'][-1] = f'from pathlib import Path; Path({str(counter)!r}).touch()'
        for fail_at in (8, 9):
            self.intent['operation'] = f'stop-{fail_at}'
            original = self.m.sync_dir
            calls = [0]
            def failure(path):
                calls[0] += 1
                if calls[0] >= fail_at:
                    raise OSError('continuous supervisor fsync failure')
                return original(path)
            with patch.object(self.m, 'sync_dir', failure):
                pid = self.submit()
            self.assertEqual('UNKNOWN', self.finish(pid)['state'])
            self.assertFalse(counter.exists())


if __name__ == '__main__':
    unittest.main()
