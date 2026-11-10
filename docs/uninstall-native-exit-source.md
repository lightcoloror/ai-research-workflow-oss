# NSSM uninstall exit handling

The existing Windows service wrapper now checks LASTEXITCODE immediately after each NSSM stop/remove command and throws with the operation and exit value on failure. A failed stop prevents removal; a failed removal cannot reach the final removed:true JSON. Other source, including the ScheduledTask branch, is unchanged.

Run the isolated synthetic regression from the source root:

    pwsh -NoProfile -File tests/test_uninstall_native_exit.ps1

This test reads the wrapper as AST data and executes only its extracted NSSM clause and final JSON statement with an in-memory PowerShell function. It never invokes the complete wrapper, NSSM, services or Task Scheduler. On PowerShell 7.5.2, five current-source controls passed (zero codes, stop/remove nonzero, stop/remove throwing); a sixth control reproduced the prior unguarded false-success behavior. Syntax parsing passed. These are function-substitution controls, not actual native-process exit-code or service integration tests.

External NSSM is not bundled; command discovery does not authenticate its identity. ScheduledTask, Windows PowerShell 5.1 and real operations remain unverified. A zero-code removed:true report does not independently prove SCM absence. Actual uninstall operations require separate authorization. No dependencies or runtime settings were added.
