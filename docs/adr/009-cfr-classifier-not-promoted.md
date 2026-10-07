# ADR 009: Do not promote the CFR-part MLP

- Status: Accepted
- Date: 2026-10-07

## Context

`app/services/classifier.py` is add-one naive Bayes. Retrieval may use it as an optional rerank
prior. [ADR 008](008-hybrid-retrieval-default.md) left that prior off the default path.

`ml/cfr_classifier/train.py` compares that baseline with a majority ranking, a TF-IDF logistic
regression, and a small PyTorch MLP. The split is publication date: train before 2024-07-01,
test on 2024-07-01 and after. Labels need five training documents. Text is the title plus the
first 4000 characters of the body. The torch heads use raw tokens. Naive Bayes still uses the
synonym-expanded token set from the retrieval prior.

`evals/reports/cfr_classifier_2026-10-07.json` records the run at `2026-10-07T15:03:19Z`, git
`fef5cea5bd6135ee49abcddb263065403c7ee054`, dataset `840a6be0a156`, torch `2.14.1+cpu`. 409
training documents and 99 test documents remained after the support filter. 65 earlier documents
and 15 later documents had no label that met the training support.

Top-1 hit rate means the first predicted part is one of the document's CFR parts. Recall@3 is
the mean fraction of those parts found in the first three predictions.

| Model | Top-1 hit rate | Recall@3 |
| --- | --- | --- |
| majority | 0.5252525252525253 | 0.7676767676767676 |
| naive Bayes | 0.8383838383838383 | 0.9494949494949495 |
| TF-IDF logistic regression | 0.8484848484848485 | 0.9595959595959596 |
| MLP | 0.8383838383838383 | 0.9090909090909091 |

The MLP ties naive Bayes on top-1 and is below both naive Bayes and logistic regression on
Recall@3. Logistic regression is the strongest of the four on both metrics. The majority top-1
of 0.5252525252525253 is the `40:52` share of the test window.

Gold population stability between the two windows is 0.6884761992732462. The cutoff in code is
0.25, so `gold_drifted` is true. The TF-IDF mean still moved: L2 shift 0.141372630378354, with
the first two principal directions explaining 0.1557835877068428 and 0.02764487295965721 of
training variance. Five trained labels (`40:1037`, `40:372`, `40:721`, `40:86`, `40:9`) do not
appear in the test window.

## Decision

Do not promote the MLP. `python -m ml.cfr_classifier.promote` exits 1 on this report, and
`data/models/cfr_mlp.promoted.safetensors` is not written. The safetensors files for the run
stay under `data/models/`, which is gitignored. Their SHA-256 values are in the report.

The comparison does not approve, reject, notify, or edit a policy. Default retrieval stays
hybrid, without this classifier and without the naive Bayes prior.

## Consequences

A later candidate has to match or beat majority, naive Bayes, and TF-IDF logistic regression on
both top-1 and Recall@3, and the held-out label mix has to stay within the 0.25 stability
cutoff. Beating those gates would still only keep an artifact. A person reviews every case.
