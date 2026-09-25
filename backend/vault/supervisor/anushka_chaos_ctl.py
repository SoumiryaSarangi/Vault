"""Chaos controller: power cut/restore, link cut, node chaos, faults list, script runner, add node,
reset, seed endpoint (ARCHITECTURE §6, §7.4). Every action reports ground truth to metadata
(POST /v1/incidents/fault + an ExternalEvent chaos.*). Owner: Anushka. Tasks A2–A3.
"""
