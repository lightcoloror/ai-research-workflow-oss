# Offline release audit

Use this script on a frozen source staging directory before publishing it.
It checks known runtime paths and selected text files for local paths and
credential-bearing URL patterns. Use it alongside source and license review
and a dedicated secret scanner.

Create a UTF-8 JSON policy outside the staged source directory. It must
contain these four nonempty Python regular expressions. The values below
are synthetic examples. Replace them with the patterns for your environment
and keep your policy private.

```json
{
  "windows_user_home": "(?i)C:[/\\\\]Users[/\\\\]ExampleOnly",
  "syncthing_vault": "(?i)/example-only/vault/",
  "personal_documents": "(?i)/example-only/personal-documents/",
  "hardcoded_workspace_path": "(?i)/example-only/workspace/"
}
```

```sh
python scripts/audit_release.py ./staged-source --local-policy ../private-release-policy.json
```

The first three policy patterns apply to the declared text file types. The
workspace pattern applies to the portable source paths listed in the script.
The audit skips Git metadata and checks known excluded filenames, directory
names, and file extensions. Undeclared binary formats are outside text
inspection. A configured expression can be specific to one operating system.
Known excluded directories and entries reported as links or reparse points
are blocked before descent or text reads. Unreadable or invalid UTF-8 files
with declared text extensions block the audit.

Exit 0 indicates no findings under the supplied policy. Exit 2 indicates a
finding or an audit that could not complete. The JSON report includes the
source root and relative finding paths. Keep it local. The optional
`--output` argument saves a report to the chosen destination. Choose a path
separate from the policy file and preferably outside the staged tree.

Synthetic command checks covered retained rules, missing and invalid policy,
scope, and unreadable text. Two checks substituted filesystem metadata for
a symlink and a directory reparse point. Actual Windows links and junctions
were not created or exercised.

Run on a stable staging directory under your control. The audit does not
provide isolation against concurrent filesystem changes, verify licenses, or
certify that a tree is safe to publish.
