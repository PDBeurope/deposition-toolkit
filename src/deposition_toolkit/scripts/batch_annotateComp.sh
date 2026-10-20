set -euo pipefail

ANNOTATE="/nfs/public/services/onedep_gpfs/resources/tools/tools-centos-7.9/packages/cc-tools-v2/bin/annotateComp"
INROOT="${1:?Usage: $0 <inroot> <outroot>}"
OUTROOT="${2:?Usage: $0 <inroot> <outroot>}"
SCRIPT_DIR="."
INROOT="${INROOT%/}"
OUTROOT="${OUTROOT%/}"

echo "Sourcing ${SCRIPT_DIR}/cc_env.sh"
source "${SCRIPT_DIR}/cc_env.sh"

mkdir -p "$OUTROOT"

TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

find "$INROOT" -type f -name '*.cif' | while IFS= read -r infile; do
  rel="${infile#${INROOT}/}"
  subdir="$(dirname "$rel")"
  stem="$(basename "$infile" .cif)"

  mkdir -p "$OUTROOT/$subdir"
  temp_cif="$TMPDIR/${stem}.cif"

  "$ANNOTATE" -vv \
    -i "$infile" \
    -o "$temp_cif" \
    -op "stereo-cactvs|aro-cactvs|descriptor-oe|xyz-ideal-corina|xyz-model-h-oe|fix"

  "$ANNOTATE" -vv \
    -i "$temp_cif" \
    -o "$OUTROOT/$subdir/${stem}.cif" \
    -op "descriptor-cactvs|name-oe|descriptor-inchi|rename"

  rm -f ./__LIG__CCEX__TMP*
done