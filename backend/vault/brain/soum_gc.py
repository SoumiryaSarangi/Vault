"""run(ctx): every 5 s abort expired uploads, trim aborted + superseded fragments, trim over-replication keeping max IFL. §4.10. Task S8.
Owner: Soum. Uses only vault.common.brain_api.BrainContext (never import jaiveer_* privates except jaiveer_fate/jaiveer_placement).
"""
