# Security Model

CodeUp is a learning sandbox, not a hardened multi-tenant code execution service. It is designed for classroom use, local demos, and supervised pilots.

## Execution Boundary

- User Python runs in a separate subprocess for each `/run` request.
- Each browser session receives its own signed-session workspace.
- The runner applies an AST audit before execution.
- Imports are limited to `math`, `random`, `string`, and `datetime`.
- Dangerous builtins such as `eval`, `exec`, `compile`, `open`, and direct import access are blocked.
- Runtime is capped with a wall-clock timeout.
- Trace collection is capped to avoid unbounded event growth.
- On POSIX systems, CPU time and address space limits are also applied through `setrlimit`.

## Web Boundary

- State-changing routes enforce same-origin checks outside testing mode.
- Snippets and sandbox files are scoped to the active signed session.
- The app rate-limits execution requests per session.
- AI calls are optional and can be disabled with `GEMINI_ENABLED=0`.

## Not Guaranteed

- Do not expose this app as an unsupervised public code execution service.
- Windows cannot enforce the same POSIX `setrlimit` CPU and memory caps.
- Browser speech recognition availability depends on the browser; Chrome and Edge are best supported.
- AI responses are not trusted as policy decisions.
- The sandbox reduces risk but is not a replacement for container isolation, VM isolation, or a dedicated judge service.

## Reporting

Please open an issue with:

- The route or feature involved
- Minimal code or steps to reproduce
- Browser and OS
- Whether AI features were enabled
- Whether the issue requires authenticated or same-origin access
