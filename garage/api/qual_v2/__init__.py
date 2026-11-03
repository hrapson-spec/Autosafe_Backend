"""qual_v2: qualification receipt + QualifiedBundle v2 (INT route-to-live step 7). Stdlib-only, Python >= 3.9.

Design: a QualifiedBundle v2 is a BundleV1 directory, byte-for-byte unchanged, plus a sidecar QUALIFIED_V2.json and a qualification/ evidence folder.
Old v1 readers and the scorer keep seeing manifest.json (qualified:false, shadow). Nothing here edits T2c/rev3 or wt-garage.
"""
FORMAT = "QualifiedBundleV2"
