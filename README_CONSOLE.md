# Avito Parser - Console Version

Modified version of the Avito parser with the following changes according to requirements:

## Changes Made

### ✅ Removed Features
- ❌ **Telegram notifications** - No more bot integration or message sending
- ❌ **Price change tracking** - Simplified database to track only processed items (no price history)
- ❌ **GUI interface** - Console-only operation

### ✅ Added Features  
- ✅ **PostgreSQL support** - Can now export to PostgreSQL in addition to SQLite
- ✅ **Local IP switching** - Option to work with local IP instead of proxy
- ✅ **Configurable proxy usage** - Can enable/disable proxy usage
- ✅ **Console-only interface** - Simple command-line operation
- ✅ **Improved configuration** - Better control over features

## Installation

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. For PostgreSQL support (optional):
```bash
pip install psycopg2-binary
```

## Configuration

Edit `config.toml`:

```toml
[avito]
urls = ["https://www.avito.ru/..."]

# Database settings
database_type = "sqlite"  # or "postgresql"  
# database_url = "postgresql://user:password@localhost/avito_parser"  # for PostgreSQL

# IP and Proxy settings
use_proxy = false        # Enable/disable proxy usage
use_local_ip = true      # Enable local IP switching
proxy_string = ""        # Proxy configuration (if use_proxy = true)
proxy_change_url = ""    # Proxy IP change URL (if use_proxy = true)

# Other settings...
pause_general = 60
pause_between_links = 5
# ... (rest of configuration)
```

## Usage

### Console Version
```bash
# Run once and exit
python console_parser.py --once

# Run continuously (loop)
python console_parser.py

# Use custom config file
python console_parser.py --config my_config.toml

# Verbose logging
python console_parser.py --verbose
```

### Original CLI Version (still works)
```bash
python parser_cls.py
```

## Database Options

### SQLite (Default)
```toml
database_type = "sqlite"
```
Data stored in local `database.db` file.

### PostgreSQL
```toml
database_type = "postgresql"
database_url = "postgresql://username:password@localhost:5432/avito_parser"
```

Make sure PostgreSQL is running and the database exists.

## IP/Proxy Options

### Local IP (Default)
```toml
use_proxy = false
use_local_ip = true
```
Uses local internet connection with IP change simulation.

### Proxy Usage
```toml
use_proxy = true
use_local_ip = false
proxy_string = "username:password@proxy.server:port"
proxy_change_url = "https://api.proxy.com/change-ip?key=..."
```

### No IP Management
```toml
use_proxy = false
use_local_ip = false
```
No IP changes, direct connection only.

## Output

- **Excel files**: Saved to `result/` directory
- **Database**: Item IDs stored to prevent re-processing (no price tracking)
- **Logs**: Console output and `logs/app.log`

## Cross-platform Support

The application works on:
- ✅ Windows
- ✅ Linux  
- ✅ macOS

## Migration from Original Version

The new version is backward compatible with existing configurations, but:

1. Telegram settings are ignored (no error, just not used)
2. Price change tracking is disabled (simplified database schema)
3. GUI is not available in console version

To use old behavior, run the original `AvitoParser.py` (with GUI) or `parser_cls.py` (original CLI).