"""On-disk fragments: data/<node>/blobs/<vid[2:4]>/<fid>.blk (magic VLT1 + header_len + header JSON + payload), tmp/, quarantine/, node.json. Atomic write (TECH_STACK §6.3), startup cleanup, index. §3.6, §4.14. Task S2.
Owner: Soum.
"""
