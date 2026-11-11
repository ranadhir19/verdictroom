# Maintainer capacity and support burden

- Two maintainers, ~6 hours/week each, both with day jobs. Review
  latency on external PRs already averages 19 days.
- A public plugin API creates permanent obligations: versioning,
  deprecation policy, plugin-caused bug reports that must be triaged
  before they can be routed, and security questions when a plugin
  misbehaves on a user's server.
- Precedent in comparable projects: once a plugin API ships, roughly
  a third of new issue volume becomes plugin-ecosystem support
  rather than core bugs.
- Internal refactoring (no public API) carries none of the support
  obligations, but also captures none of the contributor energy: the
  9 known forks exist because there is no sanctioned way to extend.
- Both maintainers agree the parse/output coupling must be reduced
  regardless of the plugin decision; they disagree on whether the
  public API should come with it.
