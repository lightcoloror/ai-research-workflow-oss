# Windows UI launcher source

This optional launcher uses Python 3.11 or later and the source checkout.
From the repository root, initialize the local project configuration using the
Python CLI, then use scripts/start-ui.ps1 in a PowerShell environment where
local scripts are permitted. The launcher accepts loopback addresses only.
The attached mode is the default. Pass -Background for a background process.

The optional port helper, port record and SearXNG helpers are local inputs
selected explicitly by the operator. Supply only trusted helper scripts.
SearXNG management, configuration updates and browser opening are opt-in.
Check which service owns an existing PID record before using any stop or
restart action.

Background startup publishes a plain decimal PID after a successful health probe.
It stages the record in the same directory and attempts a move that refuses
to overwrite a destination. Any existing record, including a stale one, can
block startup. This behavior applies to this PowerShell launch attempt on the
local filesystem. The separate Python lifecycle may still alter that path.
Failure cleanup targets the child process started by this attempt and its
staging file.

The included synthetic checker extracts two functions and uses mock child and
health calls with task-local files. Windows PowerShell declined to load the
checker before any controls ran. The source was parsed. Actual startup, helper
execution, PowerShell 5.1 behavior and background lifecycle remain unverified.
