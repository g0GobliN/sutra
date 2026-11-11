# SUTRA API Versioning Policy

## Overview

This document defines SUTRA's API versioning policy, including semantic versioning, backward compatibility guarantees, and versioning conventions. It provides guidance for maintainers, integrators, and users on how to manage API changes and version transitions.

## Semantic Versioning

SUTRA follows Semantic Versioning (SemVer) for its public APIs:

### Version Format
`MAJOR.MINOR.PATCH`

### Version meanings
- **MAJOR**: Backward-incompatible changes, new functionality, breaking changes
- **MINOR**: Backward-compatible additions, new features, extensions
- **PATCH**: Backward-compatible bug fixes, security patches

### Version Bumps
- **MAJOR**: When removing deprecated features, changing behavior, or breaking compatibility
- **MINOR**: When adding new features, new API endpoints, or extensions
- **PATCH**: When fixing bugs, security issues, or minor improvements

## API Contracts

### External APIs
The following APIs are considered external (public) and subject to SemVer:

1. **SUTRA Core Public API (`core.py`)**
   - `process_result()`, `process_text()`, `restore()`, `outcome_json()`

2. **CLI Adapter (`scripts/sutra-cli.py`)**
   - All command-line interfaces and JSON contracts

3. **MCP Server (`scripts/sutra-mcp-server.py`)**
   - JSON-RPC protocol and tool contracts

4. **Configuration Interface (`config.py`)**
   - Public configuration resolution API

5. **Store Public Interface (`store.py`)**
   - Core storage operations (not internal implementation details)

6. **Recovery Protocol (`recovery.py`)**
   - `sutra_restore` protocol and response formats

### Internal APIs
Internal modules are not versioned but follow these guidelines:

- **Engine (`engine.py`)**: Public classes and methods that can be imported by external code
- **Core (`core.py`)**: Public functions only (internal adapters are not external APIs)
- **Plugin interfaces**: Only those exposed through Hermes or MCP registration

## Backward Compatibility Commitments

### Guaranteed Backward Compatibility
The following guarantees are maintained across MINOR and PATCH releases:

1. **Functional contracts**: Core functionality, compression rules, recovery protocol
2.'Assemblée compatible: JSON schema changes that preserve parsing
3. **Storage format**: Existing store entries remain readable
4. **Recovery protocol**: `sutra_restore` interface unchanged
5. **Configuration**: All existing configuration variables maintained
6. **Adapter contracts**: CLI/MCP server output formats unchanged

### Known Compatible Changes
The following changes are backward-compatible but may require user action:

1. **Configuration defaults**: Changed values still respect existing settings
2. **New store entries**: Can coexist with existing entries
3. **Extended features**: New options don't affect existing behavior when not used
4. **Performance improvements**: May change execution time but not results

### Breaking Changes
Breaking changes require a MAJOR version bump and are communicated in:

1. **Changelog entries**: Clear migration guidance
2. **Deprecation periods**: 12-month minimum notice for deprecated features
3. **Migration tools**: Scripts or guides for transitioning between versions
4. **Alternative paths**: Documentation for workarounds when direct migration isn't possible

## Version Support Policy

### Support Tiers

| Version Type | Support Duration | Guarantees |
|--------------|-----------------|------------|
| **Stable (main)** | Indefinite | All guarantees maintained |
| **Legacy (historical tags)** | Until removed | Bug fixes only |
| **Unsupported (past releases)** | Until end-of-life | No support provided |

### Support Levels

#### Level 1: Full Support
- All backward compatibility guarantees maintained
- Full test coverage for versioned APIs
- Active bug fix and enhancement work
- Documentation updates

#### Level 2: Limited Support
- Bug fixes only (no new features)
- Critical security fixes only
- Minimal testing
- Documentation maintenance

#### Level 3: End-of-Life
- No support provided
- Users directed to upgrade
- Security vulnerabilities not addressed

## Versioning Process

### Release Branch Strategy

```
main (stable)
  └── v0.x.y (latest stable)
        ├── v0.x.0 (minor release)
        ├── v0.x.1 (patch release)
        ├── v0.x.2 (patch release)
        └── v0.(x+1).0 (next minor release)
```

### Release Types

#### Patch Releases (v0.x.y)
- Bug fixes only
- Security patches
- Documentation updates
- No API changes
- Backward compatibility guaranteed

#### Minor Releases (v0.x+1.0)
- New features (with backward compatibility)
- New API endpoints (with version discovery)
- Documentation updates
- Extended test coverage
- Backward compatibility maintained

#### Major Releases (v0.y.0)
- Breaking changes
- API refactors
- Configuration changes
- Migration procedures
- Extended deprecation periods

### Pre-release Process

#### Alpha (v0.x.y-alpha)
- New feature development
- Internal testing
- Limited visibility
- May have breaking changes

#### Beta (v0.x.y-beta)
- Feature complete, vetted
- External testing
- Limited visibility
- Backward compatibility verified

#### Release Candidate (v0.x.y-rc)
- Final pre-release
- All tests passing
- Backward compatibility confirmed
- No planned breaking changes

### Version Selection Rules

1. **Public API requests**: Use latest stable release
2. **Integrations requiring stability**: Use latest stable release
3. **Testing/development**: Use latest beta or rc (if available)
4. **Critical fixes**: Use latest patch release
5. **New features**: Use latest stable release with new features

## Integration Guidelines

### Version Negotiation

#### For Clients
1. **Dependency specification**: Use compatible version ranges
   ```
   # PEP 508 example for Python projects
   sutra-agent >=0.4.0,<0.5.0
   
   # Semantic versioning example
   sutra-agent >=0.4.0,<0.5.0
   ```

2. **Feature detection**: Check for feature availability at runtime
   ```python
   # Python example
   import sutra
   if hasattr(sutra, 'process_text_v2'):
       # Feature available in newer version
   ```

3. **Fallback mechanisms**: Provide graceful degradation
   ```python
   # Example of version-aware feature selection
   def compress_output(text, tool_name="", use_v2=False):
       if use_v2 and hasattr(sutra.core, 'process_text_v2'):
           return sutra.core.process_text_v2(text, tool_name)
       else:
           return sutra.core.process_text(text, tool_name)
   ```

#### For Host Environments
1. **Version detection**: Verify capabilities at install time
   ```bash
   # Example detection script
   sutra detect > sutra-info.json
   # Parse for supported versions and features
   ```

2. **Version-specific configuration**: Adjust settings based on version
   ```ini
   # Configuration based on detected version
   [sutra]
   version = 0.4.2
   features = adaptive-budgets,structured-parsers
   compatibility_level = full
   ```

### Migration Procedures

#### Migration Strategy
1. **Assess impact**: Identify which version-specific features are used
2. **Plan timeline**: Set migration window and resources
3. **Test thoroughly**: Run integration tests against new version
4. **Phase deployment**: Migrate in stages if possible
5. **Rollback plan**: Prepare for quick reversion if issues arise

#### Migration Patterns

##### Pattern 1: Parallel Deployment
- Deploy new version alongside old
- Redirect traffic gradually
- Compare results and monitor differences
- Cut over when confident

```bash
# Example parallel deployment script
#!/bin/bash

# Deploy new version
sutra install --new-version v0.5.0

# Start monitoring
python3 monitor.py --version v0.5.0

# Gradual traffic redirection
sleep 300  # 5 minutes
# Monitor metrics
# Redirect 10% of traffic
# Monitor again
sleep 300
# Redirect more traffic
# Eventually reach 100%
```

##### Pattern 2: Feature Flag Migration
- Use feature flags to control new version usage
- Migrate gradually across different components
- Enable new features progressively

```python
# Example feature flag system
class MigrationManager:
    def __init__(self):
        self.flags = {
            'new_compression_v2': False,
            'new_recovery_protocol': False,
            'new_policy_injection': False,
        }
    
    def is_feature_enabled(self, feature_name):
        return self.flags.get(feature_name, False)
    
    def migrate_feature(self, feature_name, callback):
        if self.is_feature_enabled(feature_name):
            callback()
```

### Testing Across Versions

#### Version Compatibility Matrix

| From → To | v0.4.2 | v0.5.0 | v0.5.1 |
|-----------|--------|--------|--------|
| v0.4.2    | ✓      | ✓      | ✓      |
| v0.5.0    | ✓      | ✓      | ✓      |
| v0.5.1    | ✓      | ✓      | ✓      |

#### Test Coverage Requirements

1. **Functional tests**: Test all version-specific features
2. **Regression tests**: Ensure no behavior changes
3. **Integration tests**: Test with all supported host environments
4. **Performance tests**: Verify performance characteristics
5. **Security tests**: Test all security-relevant features

#### Automated Version Testing

```python
# Example version compatibility test
import pytest
import sutra

class TestVersionCompatibility:
    def test_backward_compatibility_v0_4_2(self):
        # Test that v0.5.0 can handle v0.4.2 store entries
        # Test that v0.5.0 can parse v0.4.2 configuration
        # Test that v0.5.0 can restore v0.4.2 markers
        pass
    
    def test_forward_compatibility_v0_5_0(self):
        # Test that new v0.5.0 features work correctly
        # Test that new v0.5.0 configuration works
        # Test that new v0.5.0 recovery features work
        pass
    
    def test_cross_version_recovery(self):
        # Test recovery between different version store entries
        pass
```

## Release Management

### Release Documentation

#### Release Notes Template

```markdown
# Release v0.5.0

## Summary
Description of major changes

## New Features
- Feature 1: Description
- Feature 2: Description

## Enhancements
- Improvement 1: Description
- Improvement 2: Description

## Bug Fixes
- Fix 1: Description
- Fix 2: Description

## Migration
### For Users
Steps required for migration

### For Integrators
API changes and migration guidance

### For Host Environments
Configuration and installation changes

## Deprecations
- Deprecated feature 1: Version to remove in
- Deprecated feature 2: Version to remove in

## Breaking Changes
- Breaking change 1: Description
- Breaking change 2: Description

## Compatibility
- Backward compatible changes
- Known incompatible changes
- Migration procedures

## Testing
- Test coverage changes
- Performance characteristics
- Security considerations

## Known Issues
- List of known issues

## Verification
- How to verify this release
- Commands to run for validation

## References
- Related documentation
- Additional resources
```

### Release Checklist

```bash
# Pre-release checklist
#!/bin/bash

# 1. Code quality checks
ruff check .
python3 -m pytest tests/ -q --tb=short

# 2. Documentation completeness
# Check that all external APIs are documented
# Verify migration guides exist
# Ensure examples are complete and correct

# 3. Compatibility verification
# Run compatibility tests across supported versions
# Test with all host environments
# Verify store migration procedures

# 4. Performance testing
# Run benchmark suite
# Compare with previous release
# Identify performance regressions

# 5. Security verification
# Run security scan
# Review for new vulnerabilities
# Verify no new attack surfaces

# 6. Documentation review
# Verify all external APIs documented
# Check for clarity and completeness
# Ensure examples work correctly

# 7. Release preparation
# Update version in pyproject.toml
# Create git tag
# Build distribution packages
# Generate release notes

# 8. Post-release validation
# Run full test suite
# Verify installation procedures
# Monitor early adopters for issues
```

## Support and Escalation

### Support Escalation Path

1. **Basic support**: Check documentation, run tests, consult version notes
2. **Intermediate support**: Contact maintainers with specific issue
3. **Advanced support**: Open issue with full context and reproduction steps
4. **Critical support**: Emergency contact for security issues

### Support Resources

#### Documentation
- [Versioning Policy](versioning.md): This document
- [Configuration docs](configuration.md): Configuration reference
- [Release notes](https://github.com/g0GobliN/sutra/releases): Release history
- [Migration guides](migration.md): Version migration procedures

#### Support Contacts
- **Primary**: SUTRA maintainers
- **Security**: security@g0gobliN.com (for security issues only)
- **Documentation**: docs@g0gobliN.com (for documentation issues)

#### Support Channels
- **GitHub Issues**: For feature requests and bug reports
- **GitHub Discussions**: For general questions and discussions
- **Email**: For support requests and escalations
- **Slack/Discord**: If available for community support

### Critical Support Procedures

#### Security Issues
1. **Immediate reporting**: Report to security@g0gobliN.com
2. **Coordinated disclosure**: Follow coordinated disclosure guidelines
3. **Patch development**: Emergency patch if critical
4. **Public notification**: Responsible disclosure as appropriate

#### Data Loss or Corruption
1. **Immediate action**: Stop using affected version
2. **Assessment**: Determine scope and impact
3. **Recovery**: Use backup procedures if available
4. **Escalation**: Contact support immediately

#### Performance Regression
1. **Rapid assessment**: Identify performance impact
2. **Root cause analysis**: Determine cause
3. **Workaround**: Provide temporary fix if possible
4. **Fix deployment**: Roll out fix as soon as possible

## References

### Related Documents
- [Configuration Management Guide](migration.md): Detailed configuration migration procedures
- [Release Process Documentation](release.md): Detailed release process
- [Security Model](SECURITY.md): Security considerations and threats
- [Adapter Integration Guide](integrations.md): Integration with host environments

### External References
- [Semantic Versioning](https://semver.org/): SemVer specification
- [PEP 508](https://www.python.org/dev/peps/pep-0508/): Python dependency specification
- [JSON Schema](https://json-schema.org/): JSON validation framework

### Internal References
- [Core API Documentation](core.py): Core public API documentation
- [CLI Interface Documentation](scripts/sutra-cli.py): CLI interface documentation
- [MCP Protocol Documentation](scripts/sutra-mcp-server.py): MCP protocol documentation

This document provides a comprehensive framework for managing SUTRA's API versioning, ensuring backward compatibility, and providing clear migration procedures for all stakeholders. It emphasizes maintaining stability while enabling innovation and feature development.