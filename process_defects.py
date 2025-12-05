import pandas as pd
import glob
import os
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("defects_processing.log"),
        logging.StreamHandler()
    ]
)

class DefectConfig:
    TEST_ITEM_FOLDER = "MOT Test Failures/MOT Testing data failure item (2024)"
    DETAIL_FILE = "item_detail.csv"
    GROUP_FILE = "item_group.csv"
    OUTPUT_FILE = "defects_summary.csv"
    ADVISORIES_OUTPUT_FILE = "advisories_summary.csv"
    CHUNK_SIZE = 1_000_000
    VEHICLE_CLASS = "4"  # Standard cars
    
    # Optional: rename DVSA categories to consumer-friendly names
    CATEGORY_RENAME = {
        "Lamps, Reflectors and Electrical Equipment": "Lights",
        "Road Wheels": "Wheels",
    }

def load_rfr_mapping(detail_file, group_file, vehicle_class):
    logging.info("Loading DVSA lookup tables...")
    
    if not os.path.exists(detail_file):
        logging.error(f"{detail_file} not found.")
        return None
        
    if not os.path.exists(group_file):
        logging.error(f"{group_file} not found.")
        return None
    
    try:
        detail = pd.read_csv(
            detail_file,
            sep="|",
            dtype=str,
            usecols=["rfr_id", "test_class_id", "test_item_set_section_id"],
            encoding="latin1"
        )
        
        groups = pd.read_csv(
            group_file,
            sep="|",
            dtype=str,
            usecols=["test_item_id", "test_class_id", "item_name"],
            encoding="latin1"
        )
    except Exception as e:
        logging.error(f"Error reading lookup files: {e}")
        return None
    
    detail = detail[detail["test_class_id"] == vehicle_class]
    groups = groups[groups["test_class_id"] == vehicle_class]
    
    mapping = detail.merge(
        groups,
        left_on=["test_item_set_section_id", "test_class_id"],
        right_on=["test_item_id", "test_class_id"],
        how="left"
    )
    
    rfr_to_category = dict(zip(mapping["rfr_id"], mapping["item_name"]))
    
    for rfr_id, category in rfr_to_category.items():
        if category in DefectConfig.CATEGORY_RENAME:
            rfr_to_category[rfr_id] = DefectConfig.CATEGORY_RENAME[category]
    
    logging.info(f"Loaded mapping for {len(rfr_to_category)} RfR codes")
    categories = set(rfr_to_category.values())
    logging.info(f"Categories found: {sorted(categories)}")
    
    return rfr_to_category

def process_defects_pipeline():
    logging.info("--- STARTING PART 1: Processing Defects ---")
    
    rfr_mapping = load_rfr_mapping(DefectConfig.DETAIL_FILE, DefectConfig.GROUP_FILE, DefectConfig.VEHICLE_CLASS)
    if rfr_mapping is None:
        return
    
    # Find all monthly test_item files
    file_pattern = os.path.join(DefectConfig.TEST_ITEM_FOLDER, "test_item_*.csv")
    all_files = glob.glob(file_pattern)
    
    if not all_files:
        logging.error(f"No test_item files found in '{DefectConfig.TEST_ITEM_FOLDER}'")
        return
    
    logging.info(f"Found {len(all_files)} monthly failure files. Processing...")
    relevant_failures = []
    total_matched = 0
    total_unmatched = 0
    advisory_counts = []  # Track advisories separately
    
    for filename in all_files:
        logging.info(f"Reading {os.path.basename(filename)}...")
        
        try:
            chunk_iterator = pd.read_csv(
                filename,
                sep=",",
                chunksize=DefectConfig.CHUNK_SIZE,
                usecols=["test_id", "rfr_id", "rfr_type_code"],
                dtype=str,
                encoding="latin1"
            )
            
            for chunk in chunk_iterator:
                # Separate failures from advisories
                failures = chunk[chunk["rfr_type_code"].isin(["F", "P"])]
                advisories = chunk[chunk["rfr_type_code"] == "A"]
                
                # Process failures (existing logic)
                failures["category"] = failures["rfr_id"].map(rfr_mapping)
                
                matched = failures["category"].notna().sum()
                unmatched = failures["category"].isna().sum()
                total_matched += matched
                total_unmatched += unmatched
                
                failures = failures.dropna(subset=["category"])
                
                if not failures.empty:
                    chunk_pivoted = pd.crosstab(
                        failures["test_id"],
                        failures["category"]
                    ).clip(upper=1)
                    relevant_failures.append(chunk_pivoted)
                
                # Process advisories (new: count per test)
                if not advisories.empty:
                    adv_counts = advisories.groupby("test_id").size().reset_index(name="advisory_count")
                    advisory_counts.append(adv_counts)
        except Exception as e:
            logging.error(f"Error processing file {filename}: {e}")
            continue
    
    logging.info(f"Mapping stats: {total_matched:,} matched, {total_unmatched:,} unmatched")
    if total_matched + total_unmatched > 0:
        match_rate = total_matched / (total_matched + total_unmatched) * 100
        logging.info(f"Match rate: {match_rate:.1f}%")
    
    logging.info("Consolidating data...")
    if relevant_failures:
        final_df = pd.concat(relevant_failures).groupby("test_id").max()
        final_df.index = final_df.index.astype(str)
        final_df.to_csv(DefectConfig.OUTPUT_FILE)
        logging.info(f"SUCCESS: Saved {len(final_df):,} test records to '{DefectConfig.OUTPUT_FILE}'")
        logging.info(f"Columns: {list(final_df.columns)}")
    else:
        logging.error("No matching defects found.")
    
    # Save advisory counts
    if advisory_counts:
        advisories_df = pd.concat(advisory_counts).groupby("test_id").sum().reset_index()
        advisories_df.set_index("test_id", inplace=True)
        advisories_df.index = advisories_df.index.astype(str)
        advisories_df.to_csv(DefectConfig.ADVISORIES_OUTPUT_FILE)
        logging.info(f"SUCCESS: Saved {len(advisories_df):,} advisory records to '{DefectConfig.ADVISORIES_OUTPUT_FILE}'")
    else:
        logging.info("No advisories found to save.")

if __name__ == "__main__":
    process_defects_pipeline()
