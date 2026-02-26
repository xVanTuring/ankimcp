#!/bin/bash
set -euo pipefail

# Script to install AnkiMCP as a local Anki addon

# --- Single-responsibility functions ---

find_addon_dir() {
    if [ -d "$HOME/.local/share/Anki2/addons21" ]; then
        echo "$HOME/.local/share/Anki2/addons21"
    elif [ -d "$HOME/Library/Application Support/Anki2/addons21" ]; then
        echo "$HOME/Library/Application Support/Anki2/addons21"
    elif [ -n "${APPDATA:-}" ] && [ -d "$APPDATA/Anki2/addons21" ]; then
        echo "$APPDATA/Anki2/addons21"
    else
        echo "Error: Could not find Anki addons directory" >&2
        echo "Please ensure Anki is installed" >&2
        return 1
    fi
}

prepare_addon_dir() {
    local addon_dir="$1"

    if [ -d "$addon_dir" ]; then
        echo "Removing existing installation..."
        rm -rf "$addon_dir"
    fi

    mkdir -p "$addon_dir"
    echo "Copying addon files..."
    cp -r src/ankimcp/* "$addon_dir/"
}

write_meta_json() {
    local addon_dir="$1"

    cat > "$addon_dir/meta.json" << EOF
{
    "name": "AnkiMCP",
    "mod": $(date +%s),
    "config": {
        "host": "localhost",
        "port": 4473
    }
}
EOF
}

# --- Entrypoint ---

main() {
    local addon_base addon_dir

    addon_base="$(find_addon_dir)"
    addon_dir="$addon_base/ankimcp"

    echo "Installing AnkiMCP to: $addon_dir"
    prepare_addon_dir "$addon_dir"
    write_meta_json "$addon_dir"

    echo ""
    echo "Installation complete!"
    echo ""
    echo "Next steps:"
    echo "1. Restart Anki"
    echo "2. The MCP server will start automatically when you open your profile"
    echo "3. Configure your AI assistant to connect to localhost:4473"
    echo ""
    echo "To configure the addon in Anki:"
    echo "Tools → Add-ons → AnkiMCP → Config"
}

main "$@"
