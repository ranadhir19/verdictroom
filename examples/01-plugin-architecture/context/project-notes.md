# loglens project notes (maintainer log, condensed)

- Single Python package, ~14k lines, four internal areas: ingest
  (file/syslog/journal readers), parse (regex + grok-style patterns),
  analyse (aggregations, anomaly flags), output (table, JSON, CSV).
- The parse area is the coupling hotspot: output formatters import
  parse internals directly in 11 places, and two ingest readers share
  a private buffer class with analyse.
- Releases: roughly monthly. Test suite covers ~70% of lines; the
  output formatters are the least tested area.
- The last refactor (splitting ingest from parse) took three release
  cycles and introduced two regressions that reached users.
- Installs are mostly via pip; a Homebrew formula exists. Median user
  is a sysadmin or SRE running it on servers, often offline.
