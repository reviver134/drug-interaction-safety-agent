# Render deployment

The root `render.yaml` provisions a Docker web service and a private FalkorDB
service in Singapore. Both use paid compute; the graph also uses a 1 GB disk.
Review Render's displayed estimate before creating the Blueprint.

1. Put this project in its own GitHub repository. Do not publish `.env`, `data/`,
   database snapshots, or virtual environments. The parent directory's Git remote
   is for another project and must not be used for this deployment.
2. In Render, choose New > Blueprint, connect that repository, and select
   `render.yaml`. Supply `GEMINI_API_KEY` as a secret. Leave it empty if photo
   extraction is not needed.
3. Create the services. The graph starts empty: transfer the existing populated
   graph before using the application. An empty graph does not contain interaction
   evidence, even if `/api/health` responds successfully.
4. Verify `/api/health` reports the expected nonzero drug count, resolve a known
   medication, and compare a known interaction with the local app.

## Graph transfer

The local database container is named `falkordb`. Its data directory is
`/var/lib/falkordb/data`, which is also the persistent mount in the Blueprint.
Use a compatible FalkorDB image version at both ends for snapshot transfers.
Before transferring any records, verify that the graph contains only the data
you intend to host, including any patient records.

A Redis RDB snapshot can be exported locally using:

```powershell
docker exec falkordb redis-cli SAVE
docker cp falkordb:/var/lib/falkordb/data/dump.rdb ./data/render-dump.rdb
```

Transfer this snapshot privately to Render and restore it into the new database
with the database stopped and its persistent disk mounted. Do not replace files
under a running database. Render's disk maintenance/restore procedure must be
completed before enabling AOF on an imported RDB; existing AOF files take
precedence over RDB files. Alternatively, run the project loaders against the
private database from a temporary Render service with access to the source data.
Do not expose port 6379 publicly for the import.

## Public deployment behavior

The Blueprint sets `ENABLE_PATIENT_MUTATIONS=false`, disabling legacy patient
add/remove and demo-seeding HTTP endpoints. Prescription analysis remains
available. Local behavior is unchanged unless this variable is set.
Public Gemini requests are not authenticated or rate limited; use a restricted
provider quota for a demo or add access control for a private deployment.

Bulk source data is excluded from the app image. Normal prescription matching
uses the hosted graph and bundled `medication_aliases.json`; rebuilding the graph
requires the external source data. Keep graph backups separately from the disk.
