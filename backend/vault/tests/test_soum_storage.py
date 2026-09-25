"""Owner: Soum (S2). Cover: .blk round trip (header + payload); atomic write leaves no tmp on success;
startup deletes tmp/*.part and counts them; corrupt payload detected on read → quarantined; index rebuilt from disk."""
