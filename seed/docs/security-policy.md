# Security policy

## Access control

Access to production data requires single sign-on and multi-factor authentication. The analytics service connects with the ops_readonly role. That role has SELECT privileges only, a five second statement timeout and read-only transactions.

## Least privilege

No analytics process has DDL, write or role-management privileges. Migration credentials are held by the deployment pipeline and are never available to the query service. Secrets are stored in the platform secret manager.

## Logging and review

Every query is logged with a hash of the SQL, row count and duration. Access reviews happen quarterly. Privilege changes require two approvals and are recorded in the change log.

## Incident response

A suspected data incident is reported to the security team within one hour. The team can revoke credentials, rotate secrets and isolate the service. Post-incident reviews are blameless and produce tracked actions.
