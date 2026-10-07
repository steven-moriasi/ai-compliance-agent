# Retrieval evaluation

Recorded 2026-10-07T13:06:56.465512+00:00. Numbers come from the JSON report written beside this file.

| set | mode | items | Recall@1 | Recall@5 | Recall@10 | MRR@10 | nDCG@10 | p50 ms | p95 ms |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| document_level_v1 | keyword | 199 | 0.3317 | 0.7035 | 0.7688 | 0.4922 | 0.3379 | 875.3876 | 2464.2435 |
| document_level_v1 | fulltext | 199 | 0.1106 | 0.2060 | 0.2412 | 0.1491 | 0.0669 | 26.1741 | 772.3635 |
| document_level_v1 | vector | 199 | 0.4221 | 0.7286 | 0.7789 | 0.5622 | 0.3895 | 147.9058 | 236.1814 |
| document_level_v1 | hybrid | 199 | 0.3970 | 0.7286 | 0.7789 | 0.5488 | 0.3609 | 211.0728 | 960.5293 |
| document_level_v1 | hybrid_prior | 199 | 0.4020 | 0.7286 | 0.7789 | 0.5513 | 0.3609 | 209.7125 | 1011.8962 |
| section_level_v1 |  |  | missing |  |  |  |  |  |  |
| curated_v1 | keyword | 14 | 0.0000 | 0.0714 | 0.2857 | 0.0714 | 0.1145 | 481.8745 | 2440.9642 |
| curated_v1 | fulltext | 14 | 0.2143 | 0.4286 | 0.6429 | 0.3158 | 0.2908 | 123.3157 | 408.0107 |
| curated_v1 | vector | 14 | 0.1429 | 0.5000 | 0.5714 | 0.3006 | 0.2395 | 109.2260 | 176.0078 |
| curated_v1 | hybrid | 14 | 0.4286 | 0.6429 | 0.7143 | 0.5281 | 0.3003 | 226.4761 | 656.6294 |
| curated_v1 | hybrid_prior | 14 | 0.4286 | 0.6429 | 0.7143 | 0.5298 | 0.3007 | 382.7496 | 947.2414 |
