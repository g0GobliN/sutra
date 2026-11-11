# SUTRA Configuration Migration Guide

## Overview

This document describes how to migrate SUTRA configuration between versions, ensuring backward compatibility and proper behavior preservation. It covers changes introduced in recent major releases and provides step-by-step migration procedures.

## Version Migration Paths

### v0.4.0 → v0.5.0

**Changes:**
- Added agent policy prompt guidance through Hermes plugin API
- Introduced exact-duplicate reuse feature
- Enhanced CLI status reporting with optimization state
- Fixed per-client error isolation

**Migration Steps:**
1. **Review configuration changes**: Check if any custom `SUTRA_*` environment variables are no longer supported
2. **Policy mode migration**: If using custom policy text, update to new format
3. **Reuse configuration**: Set `SUTRA_REUSE=1` (default) to enable exact-duplicate reuse
4. **Backup before changes**: Run `sutra doctor` before modifying host configurations

### v0.3.0 → v0.4.0

**Changes:**
- Added Codex CLI automatic integration
- Enhanced store permission security
- Improved diagnostic reliability

**Migration Steps:**
1. **Codex integration**: Re-run `sutra install --client codex` to update hooks
2. **Store permissions**: If using custom `SUTRA_STORE_DIR`, ensure proper permissions (`0700` for directory, `0600` for files)
3. **Configuration backup**: Existing configurations preserved with timestamped backups

### v0.2.0 → v0.3.0

**Changes:**
- Added Hermes automatic installer
- Enhanced OpenCode adapter
- Added read-only `sutra detect`

**Migration Steps:**
1. **Hermes plugin**: Re-run `sutra install --client hermes` for plugin validation
2. **OpenCode config**: Verify plugin path and interpreter availability

## Configuration Management Best Practices

### Before Migration

```bash
# 1. Capture current configuration
sutra doctor

# 2. Export environment variables
export SUTRA_ENABLED
export SUTRA_MAX_BYTES
# ... other critical variables

# 3. Create backup of affected host configs
mkdir -p ~/.sutra-backups/$(date +%Y%m%d_%H%M%S)
```

### During Migration

```bash
# 1. Dry-run install to preview changes
sutra install --dry-run --client hermes

# 2. Apply changes incrementally
for client in hermes claude opencode codex openclaw; do
    sutra install --client $client
    sutra doctor
    # Verify after each client
    # Restore if issues occur
    sutra uninstall --client $client
    # Fix issues, then reinstall
    sutra install --client $client
    sutra doctor
    # Success → proceed to next client
    # Note: order matters for dependent clients
    # Codex requires MCP recovery entry first
    # OpenCode may need Node.js for full verification
    # OpenClaw requires isolated npm prefix
    # OpenCode's `sutra install` creates ~/.config/opencode/plugins/sutra.js
    # which references a bundled interpreter that must be executable
    # — skip full verification if interpreter not available
    # Proceed to next client after this one
    # The installer logs per-client summary for manual review
    # Documentation: https://github.com/g0GobliN/sutra/blob/main/docs/automatic-install.md
    # For each client, verify:
    #   - configuration file updated
    #   - backup created
    #   - plugin validation passed
    #   - host restarts completed
    #   - recovery capability tested
    # If verification fails, restore backup and retry
    # If rollback required:
    #   sutra uninstall --client $client
    #   restore from backup
    #   investigate root cause
    #   fix and retry
    # After successful migration:
    #   run sutra doctor to confirm all adapters
    #   test with representative fixture
    #   validate recovery with sutra restore
    # If tests pass, commit the migration
    # record version in migration log
    # proceed to next client
    # end
    # end
```

### After Migration

```bash
# 1. Validate all adapters
sutra detect

# 2. Test compression/restore round-trip
python3 bench/benchmark.py --check

# 3. Run adapter contract tests
pytest tests/test_adapter_contract.py -q

# 4. Verify host-specific functionality
for client in hermes claude opencode codex openclaw; do
    echo "Testing $client adapter..."
    # Run client-specific tests if available
    case $client in
        claude) pytest tests/test_claude_code_hook.py -q ;;
        opencode) pytest tests/test_opencode_adapter.py -q ;;
        codex) pytest tests/test_codex_hook.py -q ;;
        openclaw) pytest tests/test_openclaw_adapter.py -q ;;
    esac
    # If any test fails, rollback and investigate
    # Record migration success/failure
    # If partial success, investigate root cause
    # If rollback needed:
    #   for backup_file in backups/*.json; do
    #       restore using backup
    #   done
    #   run validation again
    #   if successful, commit migration
    #   if failed again, exit with error
    #   else:
    #       test again after fix
    #       if passed, record success
    #       else rollback and exit with error
    #   end
    # end
```

## Configuration Schema Changes

### Deprecated Variables (v0.5.0)

| Variable | Old Behavior | New Behavior | Migration |
|----------|--------------|--------------|-----------|
| `SUTRA_JSON_ITEMS` | Retired in v0.5.0 | Ignored with warning | Remove or ignore |

### Changed Variables (v0.4.0→v0.5.0)

| Variable | Old Default | New Default | Notes |
|----------|--------------|--------------|-------|
| `SUTRA_POLICY` | `off` | `off` | Same value, new semantic meaning |
| `SUTRA_REUSE` | `1` | `1` | Same value, new store verification |

### New Variables (v0.5.0)

| Variable | Purpose | Default | Notes |
|----------|---------|---------|-------|
| `SUTRA_POLICY` | Agent guidance mode | `off` | `off`, `balanced`, `aggressive` |
| `SUTRA_REUSE` | Exact-duplicate replacement | `1` | Store-verified before marking |

## Store Migration

### Volume Resizing

```bash
# To increase store capacity:
# 1. Update SUTRA_STORE_MAX_BYTES
export SUTRA_STORE_MAX_BYTES=536870912  # 512 MiB

# 2. Clear old store if needed (data already stored is preserved)
sutra uninstall  # removes SUTRA entries only
# Manually move existing store to backup
mkdir -p ~/.sutra-backups/old-store
tmv $HERMES_HOME/cache/sutra-store ~/.sutra-backups/old-store

# 3. Restart all hosts to pick up new limits
```

### Store Permissions Migration

```bash
# Modern SUTRA requires secure permissions:
# Directory: 0700 (owner read/write/execute)
# Files: 0600 (owner read/write)

# If existing store uses insecure permissions:
chmod 0700 $HERMES_HOME/cache/sutra-store
find $HERMES_HOME/cache/sutra-store -type d -exec chmod 0700 {} \;
find $HERMES_HOME/cache/sutra-store -type f -exec chmod 0600 {} \;

# If permission changes break existing adapters:
# 1. Backup the store first
mkdir -p ~/.sutra-backups/permission-migration
tar -czf ~/.sutra-backups/permission-migration/store-$(date +%s).tar.gz \
    $HERMES_HOME/cache/sutra-store

# 2. Temporarily relax permissions for migration
chmod 0755 $HERMES_HOME/cache/sutra-store
find $HERMES_HOME/cache/sutra-store -type f -exec chmod 0644 {} \;

# 3. Run adapters to verify compatibility
pytest tests/test_adapter_contract.py

# 4. If tests pass, tighten permissions
# If tests fail, restore from backup and investigate
# The store format remains compatible across permission changes
```

## Host-Specific Migration

### Hermes Agent Migration

```bash
# Migration path for Hermes v0.22.x → v0.23.x:

# Before migration (capture current state):
sutra doctor
hermes plugins list

# During migration:
# 1. Remove old plugin if present
hermes plugins disable sutra || true
rm -rf $HERMES_HOME/plugins/sutra

# 2. Install new plugin
sutra install --client hermes

# 3. Validate new plugin
hermes plugins validate sutra
hermes plugins enable sutra

# 4. Restart gateway to reload middleware
# This step depends on your Hermes deployment
# For systemd: sudo systemctl restart hermes-gateway
# For Docker: restart container with updated volumes
# For manual: kill and restart the gateway process

# 5. Verify integration
python3 bench/benchmark.py --check
```

### Claude Code Migration

```bash
# Claude Code v2.1.x → v2.2.x migration:

# 1. Backup user settings
mkdir -p ~/.claude-backups
cp ~/.claude.json ~/.claude-backups/before-migration-$(date +%s).json

# 2. Backup MCP servers if present
if [ -f ~/.config/claude/mcp.json ]; then
    cp ~/.config/claude/mcp.json ~/.claude-backups/mcp-before-$(date +%s).json
fi

# 3. Remove old SUTRA entries
python3 scripts/sutra-cli.py restore --id su_... --mode full 2>/dev/null || true

# 4. Clean up
rm -f ~/.claude.json
rm -f ~/.config/claude/mcp.json

# 5. Install new setup
sutra install --client claude

# 6. Trust the hook (one-time)
# Claude Code will prompt with /hooks command
# If auto-installed without interactive session:
# Run claude, then type /hooks
# Trust the SUTRA PostToolUse entry

# 7. Verify
claude doctor  # This should report SUTRA as configured and active
# If doctor fails:
# 1. Check if sutra install --client claude completed successfully
# 2. Verify ~/.claude.json exists and contains sutra MCP server
# 3. Ensure Claude Code trust ledger accepts the hook
# 4. Run a test compression/restore to verify functionality
```

### OpenCode Migration

```bash
# OpenCode 1.19.x → 1.20.x migration:

# 1. Backup OpenCode config
mkdir -p ~/.config/opencode/backups
cp ~/.config/opencode/config.json ~/.config/opencode/backups/before-$(date +%s).json

# 2. Remove old SUTRA plugin
rm -f ~/.config/opencode/plugins/sutra.js

# 3. Remove SUTRA MCP entry if present
python3 scripts/sutra-cli.py restore --id su_... --mode full 2>/dev/null || true

# 4. Install new setup
sutra install --client opencode

# 5. Restart OpenCode to load new plugin
# This depends on your OpenCode deployment method
# For desktop app: restart the application
# For VS Code extension: reload window
# For standalone: kill and restart process

# 6. Verify
# Check if plugin appears in OpenCode UI
# Test with a real Bash tool call
```

## Testing Migration

### Migration Test Suite

```bash
# 1. Run all adapter tests
pytest tests/test_adapter_contract.py tests/test_hermes_adapter.py \
    tests/test_claude_code_hook.py tests/test_opencode_adapter.py \
    tests/test_codex_hook.py -v

# 2. Run benchmark verification
python3 bench/benchmark.py --check

# 3. Run benchmark self-checks
python3 bench/benchmark.py --selfcheck

# 4. Run A/B runner self-check
python3 scripts/ab/run.py --selfcheck

# 5. Check for regressions in existing functionality
pytest tests/ -k "not benchmark" -v

# 6. Verify exact-duplicate reuse if enabled
# Test with identical tool results in same session
```

### Rollback Procedures

```bash
# If migration fails:

# 1. Restore from the most recent backup
LATEST_BACKUP=$(ls -t ~/.sutra-backups/*.json 2>/dev/null | head -n 1)
if [ -n "$LATEST_BACKUP" ]; then
    cp $LATEST_BACKUP ~/.claude.json
    echo "Restored Claude Code configuration from backup"
fi

# 2. For each affected host:
cat > rollback.sh << 'EOF'
#!/bin/bash
# Rollback script for SUTRA migration
# Usage: ./rollback.sh <client>

CLIENT="$1"
BACKUP_DIR="~/.sutra-backups/$(date +%Y%m%d_%H%M%S)"

mkdir -p $BACKUP_DIR

echo "Rolling back $CLIENT adapter..."

# Capture current state before rollback
sutra doctor > $BACKUP_DIR/before-$(date +%s).json

# Perform rollback based on client
case $CLIENT in
    hermes)
        # Hermes rollback
        hermes plugins disable sutra || true
        rm -rf $HERMES_HOME/plugins/sutra
        # Restore from backup if exists
        if [ -f $BACKUP_DIR/hermes.json ]; then
            cp $BACKUP_DIR/hermes.json $HERMES_HOME/plugins/sutra/plugin.yaml
        fi
        ;;
    claude)
        # Claude rollback
        rm -f ~/.claude.json
        rm -f ~/.config/claude/mcp.json
        if [ -f $BACKUP_DIR/claude.json ]; then
            cp $BACKUP_DIR/claude.json ~/.claude.json
        fi
        ;;
    opencode)
        # OpenCode rollback
        rm -f ~/.config/opencode/plugins/sutra.js
        if [ -f $BACKUP_DIR/opencode.json ]; then
            cp $BACKUP_DIR/opencode.json ~/.config/opencode/config.json
        fi
        ;;
    codex)
        # Codex rollback
        # Remove hook and MCP entry
        ;;
    *)
        echo "Unknown client: $CLIENT"
        exit 1
        ;;
esac

# Verify rollback works
sutra doctor
EOF

chmod +x rollback.sh
```

## Migration Logging

### Before Migration
```bash
# Capture current environment
env | grep SUTRA > migration.log.before

# Capture current git state
git status >> migration.log.before
git diff >> migration.log.before

# Capture current benchmarks
python3 bench/benchmark.py --check >> migration.log.before
```

### After Migration
```bash
# Capture new state
env | grep SUTRA > migration.log.after

# Capture git changes
git status >> migration.log.after
git diff >> migration.log.after

# Capture benchmark results
python3 bench/benchmark.py --check >> migration.log.after

# Generate comparison
diff migration.log.before migration.log.after > migration-comparison.log || true
```

## Compliance Checklist

### Configuration Migration
- [ ] Verify all environment variables documented
- [ ] Test host adapters with representative fixtures
- [ ] Verify exact-duplicate reuse (if enabled)
- [ ] Confirm storage isolation per profile
- [ ] Validate agent policy injection (if applicable)

### Store Migration
- [ ] Verify store permissions and ownership
- [ ] Test recovery from existing store entries
- [ ] Validate entry TTL behavior
- [ ] Confirm quota enforcement

### Host-Specific Migration
- [ ] Verify automatic installation for each supported client
- [ ] Test manual installation fallback
- [ ] Confirm restart requirements
- [ ] Validate trust mechanisms (Codex hooks)

## Emergency Procedures

### Migration Blockers
1. **Storage migration fails**: Restore from backup, investigate permission issues
2. **Host adapter fails**: Temporarily disable with `SUTRA_ENABLED=0`, investigate
3. **Policy injection fails**: Set `SUTRA_POLICY=off`, restart hosts

### Rollback Triggers
- **Test failures**: Automated rollback to most recent successful state
- **Data corruption**: Immediate rollback with backup restoration
- **Security issues**: Emergency disable and secure cleanup

## Post-Migration Verification

```bash
# 1. Full system test
python3 -m pytest tests/ -v --tb=short

# 2. Benchmark verification
python3 bench/benchmark.py --check

# 3. Adapter contract verification
pytest tests/test_adapter_contract.py -v

# 4. Integration verification (if applicable)
scripts/e2e/run.sh

# 5. Performance regression check
# Compare with previous benchmark results
```

## References

- [SUTRA Configuration](../docs/configuration.md): Current configuration reference
- [Automatic Installation](../docs/automatic-install.md): Client installation procedures
- [Universal Integration Audit](../docs/universal-integration.md): Host capability verification
- [Security Model](../SECURITY.md): Storage security considerations
- [Agent Policy](../docs/agent-policy.md): Policy injection mechanisms

This migration guide provides comprehensive procedures for safely upgrading SUTRA across versions while maintaining backward compatibility and system reliability. Always test migrations in a non-production environment before applying to production systems.