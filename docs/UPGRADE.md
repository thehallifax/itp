# Upgrade

Back up `runtime/` and Docker volumes before upgrading.

```bash
./itp update
```

Update refuses an unexpectedly dirty source tree, performs only a fast-forward
pull, rebuilds or pulls images, preserves runtime state, restarts the active
deployment, and directs the operator to Doctor.

Automatic unattended source updates are intentionally unsupported. If an
upgrade fails, retain the runtime backup, return the source checkout to the
previous reviewed revision, start the deployment, and run `./itp doctor`.
