# Data retention

## Retention periods

Order and invoice records are retained for seven years to satisfy tax law. Support tickets are retained for three years after closure. Web analytics events are retained for thirteen months. Marketing consent records are retained until consent is withdrawn plus two years.

## Deletion

After the retention period, records are deleted in the monthly purge job. The purge writes an audit entry but does not keep a copy of the deleted row. Backup copies expire thirty-five days after the purge.

## Legal holds

A legal hold suspends deletion for the affected customers or date range. Support and finance must notify the data protection officer when a hold is likely.

## Anonymization

Analytics extracts are anonymized by removing names, emails and phone numbers. Customer identifiers are replaced with salted hashes that rotate every quarter.
