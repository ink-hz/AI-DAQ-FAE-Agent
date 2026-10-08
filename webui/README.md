# AI DAQ FAE WebUI

Tracked UI source imported with `git archive a6234f6 webui` from adjacent
AI-FAE-Agent, exact revision `a6234f6be546efebb230ffc74bd9a00bebdc2814`.
The DAQ adaptations are versioned here; shared Python runtime is pinned separately.

Run `npm ci`, `npm test`, and `npm run build` in this directory.
`npm run dev` uses port 5174 and proxies DAQ API routes to
`http://127.0.0.1:8081`; set `DAQ_DEV_API_TARGET` for another Dev backend.

The server mounts built assets with `fae-browser-base` and `fae-api-base` meta
values. `/app` uses root API routes for local Dev; internal `/daq` uses
`/daq/api`. These legacy meta names and the `X-FAE-Enterprise-CSRF` header
remain transport contracts. Agent launch is bound to `ai-daq-fae-agent`;
return paths and launch URLs accept the `/daq` workspace only.

Platform integration requires a DAQ card, launch URL at
`https://agent.orbbec.com.cn/daq/`, authorized internal users, a dedicated
`/api/v1/workspaces/daq/navigation` projection, and DAQ page keys for access
reporting. This frontend does not create or authorize those Platform resources.
Absent management permission/configuration leaves the management link hidden.

Chat, attachments, feedback, durable history, source rendering and review UI
contracts are retained. Their backend routes must exist before the corresponding
flows can be accepted end to end. Review authorization must be supplied by the
controlled internal deployment; the inherited `/app/review` route is not an
access control boundary.

The inherited lens logo filename and FAE-prefixed code/type/CSS identifiers
remain. UI copy names the DAQ Agent and describes acquisition tasks; no camera
model recommendations or catalog have been embedded in the frontend.
