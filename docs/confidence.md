# Confidence

Fluffy uses different kinds of certainty for different decisions. A number from the model is not treated as a probability.

| Source | Used for | Rule |
|---|---|---|
| Tool-call confidence | Comfort | A spoken request at 0.7 or higher can act. An inferred plan at 0.7 or higher acts only when it matches the cue. A habit can act from 0.5. Otherwise Fluffy asks. Below 0.4 the plan is dropped and noted. |
| Tool-call confidence on `alert_caregiver` | Safety | 0.7 or higher is one supporting signal. It never starts a check-in by itself. |
| Agreement | Safety, gestures, object locations | Two reads, or two senses, plus a yes from the double-check. This is what starts help, changes music, or speaks a location. |
| Double-check answers | Instincts, gestures, safety | Only `yes` counts. `unclear` counts as no. |
| Transcript clear, Chrome confidence | Words | Below 0.5, the words cannot start help or a command. They become a supporting signal, or Fluffy asks "Did you say help?" |
| Scene confidence | — | Not used. The scene schema does not ask for it. |
