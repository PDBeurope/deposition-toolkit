import os
import re
import csv

# --- Core Logic Function ---
def update_cif_ligand(input_path, output_path, old_id, new_id):
    with open(input_path, 'r') as file:
        cif_content = file.read()

    # 1. Extract the systematic name string from the _pdbx_chem_comp_identifier block
    identifier_pattern = re.compile(
        r'_pdbx_chem_comp_identifier\.identifier\s*\n'
        r'\s*(?:"[^"]*"|\S+)\s+(?:"[^"]*"|\S+)\s+(?:"[^"]*"|\S+)\s+(?:"[^"]*"|\S+)\s+("[^"]*"|\S+)'
    )
    
    match = identifier_pattern.search(cif_content)
    if match:
        extracted_name = match.group(1)
        # Replace '_chem_comp.name ?' with the extracted identifier name
        cif_content = re.sub(r'(_chem_comp\.name\s+)\?', r'\g<1>' + extracted_name, cif_content, count=1)
    else:
        print(f"Warning: Could not extract systematic name for {input_path}")

    # 2. Update the data block header (e.g., 'data_LIG' -> 'data_XYZ')
    cif_content = cif_content.replace(f"data_{old_id}", f"data_{new_id}")
    
    # 3. Update all standalone occurrences of the ID using regex word boundary matching
    pattern = re.compile(rf'(?<=\s){old_id}(?=\s|$)')
    cif_content = pattern.sub(new_id, cif_content)

    # Save the updated content
    with open(output_path, 'w') as file:
        file.write(cif_content)
        
    print(f"Success: Updated {os.path.basename(input_path)} -> {os.path.basename(output_path)}")

# --- Batch Processing Function ---
def process_cif_directory(root_dir, csv_file_path):
    old_id = "LIG"

    with open(csv_file_path, 'r', newline='', encoding='utf-8') as csvfile:
        reader = csv.reader(csvfile)
        
        # Read headers and strip any trailing/leading whitespace 
        headers = [h.strip() for h in next(reader)]
        
        for row in reader:
            if not row:
                continue # Skip empty lines
                
            # Create a dictionary mapping cleaned headers to cleaned row values
            row_data = {headers[i]: value.strip() for i, value in enumerate(row)}
            
            # Process only if 'deposit' column is 'Y'
            if row_data.get('deposit') == 'Y':
                folder_path = row_data.get('Folder_path')
                new_id = row_data.get('CCD')
                
                # Construct absolute/relative paths
                input_file_path = os.path.join(root_dir, folder_path)
                
                # The output file is in the same directory as the input, named {NEW_ID}.cif
                target_directory = os.path.dirname(input_file_path)
                output_file_path = os.path.join(target_directory, f"{new_id}.cif")
                
                if os.path.exists(input_file_path):
                    update_cif_ligand(input_file_path, output_file_path, old_id, new_id)
                else:
                    print(f"Error: Target file not found at {input_file_path}")

if __name__ == "__main__":
    # Define your paths here
    ROOT_DIRECTORY = "./ccds_output/"  # Use "." for current directory, or provide an absolute path like "/path/to/data"
    CSV_FILE_PATH = "ligands_data.csv"
    
    process_cif_directory(ROOT_DIRECTORY, CSV_FILE_PATH)
