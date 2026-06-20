#!/bin/bash

# These configs will be added by
# respective parts of Harbor stack, we want to merge
# everything into one file and launch the server
echo "Harbor: custom aider entrypoint"
python --version

# Cross-integration runtime overrides: pick up secrets minted by sidecars
# AFTER Compose's create-time env substitution. Each block is a no-op if
# the sidecar mount isn't present (i.e. the integration isn't enabled).
#
# unsloth-studio: the unsloth-studio-bootstrap sidecar writes the freshly
# minted API key to ./services/unsloth-studio/.studio-auth/api_key.txt,
# which compose.x.aider.unsloth-studio.yml mounts read-only. Read it
# BEFORE the YAML merger renders aider.unsloth-studio.yml so the merged
# .aider.conf.yml carries the real key, not the placeholder.
if [ -r /run/unsloth-studio-auth/api_key.txt ]; then
    k=$(tr -d '\n' < /run/unsloth-studio-auth/api_key.txt)
    if [ -n "$k" ]; then
        export HARBOR_UNSLOTH_STUDIO_API_KEY="$k"
    fi
fi

echo "YAML Merger is starting..."
python /home/appuser/.aider/yaml_config_merger.py --pattern ".yml" --output "/home/appuser/.aider.conf.yml" --directory "/home/appuser/.aider"

# Aider searches for .aider.conf.yml in $HOME, which is /app in this image, not
# /home/appuser. Mirror the merged config there so it's auto-discovered.
if [ -n "$HOME" ] && [ "$HOME" != "/home/appuser" ]; then
    cp /home/appuser/.aider.conf.yml "$HOME/.aider.conf.yml"
fi

echo "Merged Configs:"
cat /home/appuser/.aider.conf.yml

git config --global --add safe.directory /root/workspace

echo "Starting aider with args: '$*'"
aider "$@"
