# Free Render + FalkorDB Cloud deployment

The root render.yaml provisions one free Docker web service in Singapore.
The database is hosted separately on FalkorDB Cloud. No Render private service
or persistent disk is needed. Free Render services sleep after inactivity.

## Configuration

Set these as Render environment variables, never in source control:

- FALKORDB_HOST and FALKORDB_PORT from the cloud Connectivity panel
- FALKORDB_USERNAME and FALKORDB_PASSWORD
- FALKORDB_SSL to match the cloud connection instructions (true for TLS)
- FALKORDB_GRAPH=drug_interactions
- GEMINI_API_KEY for photo extraction and generated explanations
- PORT=8000
- ENABLE_PATIENT_MUTATIONS=false

Cloud credentials are shared by the API, MCP server and loaders through
settings.graph_client(). TLS connections verify the server certificate and hostname.
Do not run the loaders against a populated graph unless you intend to rebuild it.

## Data and availability

Transfer the populated local graph before deploying. The health check at
/api/health returns 503 when the database is unavailable or has zero Drug nodes.
The bulk source files and exported graph snapshots stay in ignored data/.
Keep a local graph export and the source data so a missing cloud graph can be
restored. FalkorDB Cloud Free has no persistence; a daily query does not protect
against data loss or guarantee that a suspended instance will restart.

## Daily query

.github/workflows/falkordb-daily.yml runs a small read-only query daily at
03:17 UTC (08:47 India time) and supports manual dispatch. It runs outside
Render because a sleeping free backend cannot reliably execute scheduled jobs.
Configure the six FALKORDB connection variables above as GitHub Actions repository
secrets. A missing graph or missing Drug data fails the check without creating
an empty graph. Check GitHub Actions logs and notifications for failures.
Private-repository Actions usage counts toward the account allowance.

## Public access

The Blueprint disables legacy patient add/remove and demo-seeding HTTP routes.
Prescription analysis remains public. Gemini calls are not authenticated or rate
limited; configure a provider quota for a demo or add access control for private use.
