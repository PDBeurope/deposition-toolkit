mkdir -p out

for dir in pdb_depo_files/*; do
  [ -d "$dir" ] || continue
  name=$(basename "$dir")
  python ccd_creator.py \
    --input-dir "$dir" \
    --output-dir "out/$name"
done
