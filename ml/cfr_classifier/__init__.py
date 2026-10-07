"""Compare CFR-part classifiers on a publication-date split.

The naive Bayes baseline, a TF-IDF logistic regression, and a small PyTorch MLP
score the same held-out documents. A promotion decision can refuse an artifact.
It cannot approve a case or change retrieval.
"""
