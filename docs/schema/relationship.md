# relationship

Purpose: explain links between normalized assets. Tags are not used in version 1.
Fields/properties: `relationship_id`,
`source_asset_id`, `target_asset_id`, `relationship_type`, `confidence` (0–1 ratio),
`evidence` (string list), `first_seen`, `last_seen` (RFC3339 UTC). Units: confidence is a
ratio and times use UTC. Example type:
`observed_by` linking one API identity to an SNMP observation. Collectors: none directly;
the future Relationship Engine derives this contract from inventory evidence.
