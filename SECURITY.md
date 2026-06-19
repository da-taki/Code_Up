# Security Model

CodeUp is a learning sandbox. It is built for classroom use, local demos, and supervised pilots. It is not a hardened public code execution service.

## Execution Boundary

* User Python runs in a separate subprocess for each `/run` request.
* Each browser session gets its own signed workspace.
* Code is checked with an AST audit before execution.
* Imports are limited to `math`, `random`, `string`, and `datetime`.
* Builtins such as `eval`, `exec`, `compile`, `open`, and direct import access are blocked.
* Runtime is capped with a wall clock timeout.
* Trace collection is capped so one program cannot create unlimited events.
* On POSIX systems, CPU time and memory are also limited with `setrlimit`.

## Web Boundary

* State changing routes enforce same origin checks outside testing mode.
* Snippets and sandbox files are scoped to the active signed session.
* Execution requests are rate limited per session.
* AI calls are optional and can be disabled with `GEMINI_ENABLED=0`.

## Not Guaranteed

* Do not run CodeUp as an unsupervised public code execution service.
* Windows cannot enforce the same `setrlimit` CPU and memory caps as POSIX systems.
* Browser speech recognition depends on browser support. Chrome and Edge work best.
* AI responses are not trusted as security decisions.
* The sandbox lowers risk for demos and classroom use, but it does not replace containers, VMs, or a dedicated judge service.

## Reporting

Please open an issue with:

* The route or feature involved
* Minimal code or steps to reproduce
* Browser and OS
* Whether AI features were enabled
* Whether the issue requires authenticated access or same origin access
