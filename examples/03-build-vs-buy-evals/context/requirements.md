# What the eval system must do

- Golden datasets: a few hundred prompt/response cases at first,
  growing to a few thousand; cases are edited by all three engineers
  and occasionally by a product manager.
- Regression runs on every prompt or model change, comparing the new
  version against the current production baseline, with a readable
  diff of which cases flipped and why.
- Custom scorers are the norm, not the exception: the team's quality
  criteria include domain rules (must cite a source, must refuse
  certain categories, tone checks) that no built-in metric covers.
- A human review queue for borderline cases, with two-person sign-off
  before a prompt change ships.
- Results history must be kept for audit: which version produced
  which scores, when, and who approved the change.
