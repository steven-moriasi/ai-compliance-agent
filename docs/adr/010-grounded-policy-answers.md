# ADR 010: Answer policy questions from numbered sources with checked quotes

- Status: Accepted
- Date: 2026-10-07

## Context

Search returns sections; a reader still has to work out what they say. A local model can write
the answer, but a 3B model will also write plausible text the sections do not support, and on a
laptop CPU one call takes from about half a minute to a few minutes.

Case analysis already has the pieces: effective-dated retrieval, redaction, a worker with leases
and fencing, and quote checks against retrieved sections.

## Decision

- A question is a queued record answered by the worker, like a case. The page follows it until
  it finishes. A slow model never holds an HTTP request open, and a crashed worker's question is
  requeued by the reaper.
- The model sees numbered sources and cites them by number. Small models copy a number reliably;
  they garble section ids.
- Each source is cut at a word boundary to a configurable length so the prompt fits a small
  context window. Quotes are checked against the text the model was shown.
- An answer is shown only when every quote appears word for word in the source it names and every
  date or duration in the answer is inside a quote. One retry carries the failures as plain
  instructions. After that, or when the model says the sources do not answer the question, the
  question is unanswered and the reader gets the sources.
- The question is redacted before the model call. The audit trail records the sources and cited
  numbers, not the question text. A question is readable by its asker and by admins.

## Consequences

- A correct answer can be withheld because a quote differs by a word. That is the intended
  trade: a missing answer sends the reader to the sources; an unsupported one misleads them.
- The checks prove the quotes exist, not that the answer reads them correctly. Answers carry no
  compliance decision, and cases still go to human review.
- Trimmed sources can leave out the sentence that answers the question. Raising the limits needs
  a larger model context.
- Answer quality depends on the model and the corpus. `python -m evals.answer_eval` measures it
  on the loaded corpus; no figures are recorded until that report exists.
