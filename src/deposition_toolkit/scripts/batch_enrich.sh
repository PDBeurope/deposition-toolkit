#!/bin/bash

# Check if a directory argument was provided
if [ -z "$1" ]; then
    echo "Error: Please provide the target directory."
    echo "Usage: $0 <path_to_directory>"
    exit 1
fi

TARGET_DIR="$1"
JSON_FILE="./metadata.json" # Change this to your actual JSON path

# Loop through the structures in the provided directory
for input_file in "$TARGET_DIR"/*/*_struc.cif; do

    # Safety check: if no files match the glob, bash passes the literal glob string.
    # This prevents errors if an empty directory is passed.
    if [ ! -f "$input_file" ]; then
        echo "No *_struc.cif files found in $TARGET_DIR/*/"
        break
    fi

    output_file="${input_file%_struc.cif}_enriched_struc.cif"

    echo "Processing $input_file -> $output_file"

    python enrich_cif.py \
        --input-cif "$input_file" \
        --input-json "$JSON_FILE" \
        --output-cif "$output_file"
done