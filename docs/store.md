# SUTRA Store Operations Guide

## Overview

This document provides comprehensive guidance for operating and managing SUTRA's local storage system. It covers storage configuration, monitoring, maintenance, and troubleshooting procedures to ensure reliable and efficient operation of the SUTRA store.

### Purpose and Threat Model

The SUTRA store is a bounded, permission-restricted storage system designed to retain original tool output for recoverability. Every rewrite that promises recoverability has already paid for the bytes: the payload is in memory. Keeping a copy on local disk is what turns "45,231 bytes elided" into "45,231 bytes you can still read".

#### Threat Model and Limits:

- **Directory Security**: The directory is ``0700``: created that way, and an existing directory is tightened to it. A directory owned by another user, a symlink, or a mode that cannot be restricted makes the store refuse to operate. Every file is ``0600`` and opened ``O_NOFOLLOW`` (data and metadata alike).
- **Identifier Validation**: Identifiers are 128-bit random and validated against ``^su_[0-9a-f]{32}$`` before they touch the filesystem, so a request cannot name a path.
- **Atomic Writes**: Writes are atomic: a temp file in the same directory is renamed into place, so an interrupted write can never be read as a complete entry. There is no fsync — this is a cache, and a lost entry is reported missing rather than served wrong. A failed metadata write removes the data file it belongs to.
- **Bounded Storage**: Storage is bounded by a per-entry cap, a total quota, an entry count and a time-to-live. Writes keep an in-process usage total and sweep oldest-first only when a budget is exceeded or a sweep is due, so a write costs the same whether the store holds ten entries or the full two thousand.
- **Content Security**: Contents are never logged. Only sizes, hashes, timestamps, tool name and a truncated session id are recorded in metadata.

### Bounded Storage Model

The SUTRA store implements a strict bounded storage model with the following limits:

- **Per-entry cap**: Maximum size for any individual stored entry
- **Total quota**: Maximum combined size of all stored entries
- **Entry count**: Maximum number of entries allowed
- **Time-to-live (TTL)**: Maximum age for entries before automatic expiration

This model ensures predictable resource usage while maintaining recoverability guarantees for tool outputs.

## Store Architecture

### Directory Structure

The SUTRA store uses a flat directory structure with no indexing files:

```
$HERMES_HOME/cache/sutra-store/
├── su_1234567890abcdef1234567890abcdef.txt      # Data file for original content
├── su_1234567890abcdef1234567890abcdef.json     # Metadata file
├── su_abcdef1234567890...txt                   # Data file for original content
└── su_abcdef1234567890...json                  # Metadata file
```

Each entry consists of exactly two files:
- **Data file** (`.txt`): UTF-8 encoded binary original content
- **Metadata file** (`.json`): JSON with entry metadata (id, created_at, original_bytes, sha256, tool, session_id, version)

### Storage Components

- **Data file**: UTF-8 encoded binary original content with `.txt` extension
- **Metadata file**: JSON with entry metadata (id, created_at, original_bytes, sha256, tool, session_id, version)
- **Storage directory**: Flat structure with individual `.txt` and `.json` files, no subdirectories or index files

### Per-Profile Isolation

SUTRA respects host profile isolation:

```bash
# Default store location per Hermes profile
$HERMES_HOME/cache/sutra-store/

# Custom store location (env var)
$SUTRA_STORE_DIR/
```

#### Cross-Host Isolation

- **Hermes profiles**: Each Hermes profile has its own store
- **Claude Code**: Uses same store as its configured Hermes profile
- **OpenCode**: Uses dedicated store path
- **Codex**: Uses MCP server store isolation

## Store Configuration

### StoreConfig Fields

The `StoreConfig` dataclass defines all storage parameters:

```python
@dataclass(frozen=True)
class StoreConfig:
    directory: str                    # Storage directory path
    enabled: bool = True              # Whether store operations are enabled
    ttl_hours: int = DEFAULT_TTL_HOURS  # Time-to-live in hours (default: 7 days)
    max_bytes: int = DEFAULT_MAX_BYTES      # Maximum total storage bytes
    max_entry_bytes: int = DEFAULT_MAX_ENTRY_BYTES  # Maximum per-entry size
    max_entries: int = DEFAULT_MAX_ENTRIES     # Maximum number of entries
```

### Configuration Resolution

Configuration is resolved through `config.resolve()`:

1. **Environment variables**: `SUTRA_STORE_DIR`, `SUTRA_STORE_ENABLED`, etc.
2. **Application config**: Priority over environment variables
3. **Defaults**: Hardcoded defaults when no configuration is provided

### Default Values

- **TTL**: 7 days (168 hours)
- **Maximum storage**: 5 GB (5,242,880,000 bytes)
- **Maximum entry size**: 100 MB (104,857,600 bytes)
- **Maximum entries**: 2,000 entries

## Entry & Lookup

### Entry Dataclass

The `Entry` dataclass represents a stored entry with the following fields:

```python
@dataclass(frozen=True)
class Entry:
    id: str                     # Unique entry identifier (format: su_[0-9a-f]{32})
    original_bytes: int         # Size of original content in bytes
    sha256: str                 # SHA256 hash of original content
    created_at: float           # Unix timestamp when entry was created
    data_path: str              # Full path to data file (.txt)
    meta_path: str              # Full path to metadata file (.json)
    tool: str = ""              # Tool name that created the entry (max 64 chars)
    session_id: str = ""        # Truncated session identifier (max 64 chars)
```

### Lookup Status Values

The `Lookup` dataclass represents the result of entry lookup with the following status values:

- **"ok"**: Entry found and valid
- **"missing"**: Entry data file is missing or unreadable
- **"expired"**: Entry has exceeded TTL and was automatically deleted
- **"invalid"**: Entry ID format is invalid
- **"unavailable"**: Store is disabled or inaccessible

```python
@dataclass(frozen=True)
class Lookup:
    entry: Entry | None     # The found entry or None if not found
    status: str             # Lookup status: ok | missing | expired | invalid | unavailable
```

## Write Operations

### put() Method

The `put()` method retains text byte-for-byte and returns an `Entry` or `None`:

```python
def put(self, text: str, *, tool_name: str = "", session_id: str = "",
        now: float | None = None) -> Entry | None:
```

#### Write Process:

1. **Validation**: Check if store is enabled and text can be encoded as UTF-8
2. **Size check**: Reject text larger than `max_entry_bytes`
3. **ID generation**: Create 128-bit random identifier (`su_` + 32 hex chars)
4. **Metadata creation**: Build metadata dictionary with id, created_at, original_bytes, sha256, tool, session_id, version
5. **Atomic writes**: Write data file first, then metadata file (meta last: data file without meta is invisible)
6. **Budget enforcement**: After writing, check if quota exceeded and trigger prune if needed
7. **Failure cleanup**: If any step fails, remove both data and metadata files to avoid half entries

#### Key Features:

- **Atomic writes**: Temp file + rename ensures no partial reads
- **Failure recovery**: Complete rollback on any failure
- **Metadata-first visibility**: Data files without metadata are invisible to readers
- **No exceptions for expected failures**: Returns `None` for disabled store, encoding errors, oversized content

### Entry ID Generation

Entry IDs are generated using 128-bit random numbers:

```python
entry_id = "su_" + secrets.token_hex(16)
```

IDs are validated against the regex pattern: `^su_[0-9a-f]{32}$`

## Read Operations

### get() Method

The `get()` method looks up a retained original while enforcing TTL:

```python
def get(self, entry_id: str, now: float | None = None) -> Lookup:
```

#### Read Process:

1. **ID validation**: Check if entry_id matches `ID_RE` pattern
2. **Store availability**: Return unavailable if store is disabled
3. **Metadata reading**: Read metadata file (with O_NOFOLLOW for security)
4. **TTL enforcement**: Delete expired entries and return expired status
5. **Integrity verification**: Check data file size matches metadata
6. **Orphan cleanup**: Delete data if metadata exists without corresponding data file

#### Status Values:

- **"ok"**: Entry found and valid
- **"missing"**: Entry data file is missing or unreadable
- **"expired"**: Entry has exceeded TTL and was automatically deleted
- **"invalid"**: Entry ID format is invalid
- **"unavailable"**: Store is disabled or inaccessible

### read_bytes() Method

The `read_bytes()` method reads a slice of the stored original content:

```python
def read_bytes(self, entry: Entry, start: int = 0, length: int | None = None) -> bytes:
```

#### Features:

- **Slice reading**: Supports reading specific byte ranges
- **O_NOFOLLOW security**: Prevents symlink attacks
- **Boundary handling**: Properly handles start/length boundaries
- **OSError on failures**: Raises for missing or unreadable files

## Verification

### verify() Method

The `verify()` method confirms that an entry's bytes still hash to the recorded value:

```python
def verify(self, entry: Entry, data: bytes | None = None) -> bool:
```

#### Verification Options:

1. **Pre-read data**: When caller already has the data, compute hash directly
2. **File reading**: When data not provided, read the stored file and compute hash
3. **Security**: Uses O_NOFOLLOW for all file reads
4. **Error handling**: Returns `False` for any verification failures

#### Key Features:

- **Dual verification modes**: With or without pre-read data
- **Incremental reading**: Reads file in 1MB chunks for large files
- **O_NOFOLLOW protection**: Prevents symlink attacks during verification
- **Graceful failure**: Returns `False` instead of raising exceptions

## Housekeeping

### prune() Method

The `prune()` method performs comprehensive cleanup and quota enforcement:

```python
def prune(self, now: float | None = None) -> dict[str, int]:
```

#### Cleanup Operations:

1. **Expired entries**: Delete entries that have exceeded TTL
2. **Orphan metadata**: Remove metadata files without corresponding data files
3. **Temp files**: Remove temporary files older than 1 hour
4. **Quota enforcement**: Delete oldest entries when budget is exceeded

#### Return Report:

```python
{
    "expired": 0,      # Number of expired entries deleted
    "orphans": 0,      # Number of orphan files deleted
    "temp": 0,         # Number of temp files deleted
    "quota": 0,        # Number of entries deleted due to quota
    "bytes": 0         # Total bytes freed
}
```

### usage() Method

The `usage()` method returns current storage statistics:

```python
def usage(self) -> tuple[int, int]:
```

#### Returns:

- **entries**: Number of valid entries on disk
- **total_bytes**: Total bytes used by all entries

#### Features:

- **Authoritative scan**: Actually scans disk for real totals (not just accounting)
- **Cache refresh**: Updates in-process accounting counters
- **Ignores temp files**: Only counts valid entry files

### delete() Operation

The `delete()` method removes a stored entry and cleans up its files:

```python
def delete(self, entry_id: str) -> None:
```

#### Signature and Parameters:

- **entry_id**: Unique entry identifier (format: `su_[0-9a-f]{32}`)

#### Behavior:

1. **ID Validation**: Validates entry ID against regex `^su_[0-9a-f]{32}$`
2. **File Removal**: Removes both `.txt` (data) and `.json` (metadata) files from disk
3. **Accounting Update**: Updates in-process accounting via `_note_removed()`
4. **Silent Failures**:
   - Missing files: Silently ignores and continues (does not raise)
   - Invalid ID format: Returns immediately without operation

#### Failure Mode:

Never raises exceptions for expected failures. Silent on missing files and invalid ID format. Only raises for unexpected system errors (permissions, filesystem issues).

### Thread-Safe Accounting

The store implements per-directory, cross-process accounting using thread-safe patterns:

#### Accounting Data Structures:

- **`_ACCOUNTING_LOCK`**: Threading lock protecting shared `_ACCOUNTING` dictionary
- **`_ACCOUNTING`**: Per-directory dictionary keyed by directory path, storing `_Accounting` objects
- **`_Accounting` dataclass**: Fields include:
  - `entries`: Number of entries tracked
  - `bytes`: Total bytes tracked
  - `writes`: Number of writes tracked
  - `swept_at`: Timestamp of last sweep

#### Accounting Coordination:

- **`_ensure_accounting(now)`**: On-demand seeding of counters from disk. Called automatically on first operation per directory. Performs a full disk scan and housekeeping (pruning) during initialization.

- **`_note_write(size, now)`**: Updates accounting under lock when entries are written. Increments `entries`, `bytes`, and `writes` counters. Returns `True` when sweep is due (quota exceeded or interval threshold reached).

- **`_note_removed(size)`**: Updates accounting under lock when entries are deleted. Decrements `entries` and `bytes` counters. Thread-safe and process-safe via the shared lock.

- **`usage()`**: Authoritative disk scan under lock. Refreshes in-process accounting counters by scanning disk for real totals (ignoring temp files) and updating `_ACCOUNTING` dictionary.

#### Cross-Instance Coordination:

Concurrent `Store` instances sharing the same directory coordinate through the shared `_ACCOUNTING_LOCK` and `_ACCOUNTING` dictionary. All accounting operations are performed under lock protection to ensure consistency across processes. This prevents race conditions and ensures accurate quota enforcement and sweep triggering across the entire SUTRA system.

#### Concurrency Guarantee:

The lock protects both read and write accounting operations, ensuring that:

- Counter updates are atomic
- Quota checks are consistent
- Sweep triggering is reliable
- Cross-process coordination is maintained

## Security

### Directory Security

#### Ownership Verification

1. **POSIX systems**: Store directory must be owned by current user
2. **Windows**: Permission bits are advisory only
3. **Symlink prevention**: Store path cannot be a symlink
4. **Directory validation**: Store path must be a directory, not a file

#### Permission Enforcement

- **Directory**: 0700 permissions (owner read/write/execute only)
- **Files**: 0600 permissions (owner read/write only)
- **O_NOFOLLOW**: All file opens use O_NOFOLLOW to prevent symlink attacks
- **Directory tightening**: Existing directories are chmod 0700 on first access

### ID Validation

All entry IDs are validated against the regex pattern:

```python
ID_RE = re.compile(r"^su_[0-9a-f]{32}$")
```

This prevents path traversal attacks and ensures ID format consistency.

### Atomic Write Pattern

All writes follow a strict atomic pattern:

1. **Temp file creation**: Unique temp filename with PID and random component
2. **Exclusive creation**: Uses O_CREAT | O_EXCL flags
3. **Content write**: Write all data to temp file
4. **Atomic rename**: Replace target file with temp file
5. **Failure cleanup**: Remove temp file if any step fails

### Access Control

- **No content logging**: Original content is never logged to any system
- **Limited metadata**: Only sizes, hashes, timestamps, tool name, truncated session ID
- **Permission enforcement**: File system permissions provide access control
- **O_NOFOLLOW**: Prevents symlink-based attacks on all file operations

## Sweep Policy

### Sweep Triggers

The sweep operation (oldest-first deletion) runs under these conditions:

1. **Quota exceeded**: When entries or bytes exceed configured limits
2. **Write threshold**: Every 128 writes to the store
3. **Time interval**: Every 10 minutes (600 seconds)

### Temp File Cleanup

Temporary files are cleaned up when:

- **Age threshold**: Older than 1 hour (3,600 seconds)
- **Sweep opportunity**: During regular sweep operations

### Sweep Process

1. **Expired entry deletion**: Remove entries past TTL
2. **Orphan cleanup**: Remove metadata without data files
3. **Temp file removal**: Clean up old temporary files
4. **Quota enforcement**: Delete oldest entries to meet limits

## Error Handling

### StoreUnavailable Exception

```python
class StoreUnavailable(RuntimeError):
    """The store cannot be used safely (bad path, symlink, permissions)."""
```

This exception is raised when:

- Store directory is owned by another user
- Store path is a symlink
- Store directory has incorrect permissions
- Any other safety-critical issue is detected

### put() Error Handling

- **Expected failures**: Return `None` (store disabled, encoding errors, oversized content)
- **Unexpected failures**: Raise exceptions
- **Failure cleanup**: Always remove half-completed entries

### get() Error Handling

- **Invalid IDs**: Return "invalid" status
- **Missing metadata**: Return "missing" status
- **Corrupted data**: Delete entry and return "missing" status
- **Expired entries**: Delete entry and return "expired" status

### Housekeeping Error Handling

- **No exceptions**: `prune()` never raises
- **Graceful degradation**: Failed metrics don't affect operation
- **Logging**: Warning logs for non-critical failures

### General Principles

- **Fail-safe**: Store prefers unavailability over unsafe operation
- **Atomicity**: All operations are atomic at the filesystem level
- **Consistency**: Accounting is maintained across process boundaries
- **Recovery**: Failed operations don't leave inconsistent state
- **Monitoring**: Metrics are collected for operational visibility