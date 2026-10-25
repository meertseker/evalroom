# Security policy

## Supported versions

Only the latest released minor version receives security fixes.

## Reporting a vulnerability

Please do not open a public issue. Use GitHub's private vulnerability reporting ("Report a vulnerability" under the repository's Security tab), or email Mert Seker at meertseker@gmail.com with "evalroom security" in the subject.

Include the version, a description, and a minimal reproduction if you have one. You should get an acknowledgement within five working days and a plan within fourteen.

## Scope

evalroom has no runtime dependencies and makes no network calls. The relevant surface is:

- the JSONL store and CLI, which read and write files at paths you give them (run ids are restricted to safe file names);
- regular expressions in `matches` / `notMatches` rules, which are compiled from your rule files. Treat rule files like code: a malicious pattern can cause catastrophic backtracking.

Review data often contains customer text. evalroom never sends it anywhere, but your stores and audit files hold it; protect them accordingly.
