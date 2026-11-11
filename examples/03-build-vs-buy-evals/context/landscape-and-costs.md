# Landscape and cost notes (public reference points)

- Open-source eval frameworks are mature: dataset management,
  runners, common metrics, and basic UIs exist off the shelf. Their
  extension points vary — some make custom scorers a first-class
  plugin, others require forking core code. Community maintenance is
  uneven; picking a framework means inheriting its release cadence
  and its breaking changes.
- Teams that build in-house commonly report the first usable harness
  in 2–4 engineer-weeks, then a continuing maintenance load of a few
  days a month — plus every feature (diff views, review queues,
  history) competing with product work.
- Hosted platforms remove the maintenance load and offer polished
  annotation UIs; pricing is typically per seat plus usage, and the
  useful tiers for a team this size usually land at or above the
  stated budget ceiling within a year once usage grows. Data leaves
  the team's infrastructure unless the vendor's DPA and retention
  terms are acceptable.
- The team's scarcest resource is not money but the three engineers'
  roadmap time over the next two quarters.
