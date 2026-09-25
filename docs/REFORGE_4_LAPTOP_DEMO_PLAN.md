# REFORGE — 4-Laptop Hackathon Demo Plan

## 1. What This Demo Is Supposed to Prove

We are not demonstrating a normal file-upload website.

We are demonstrating that **four independent laptops can behave like one reliable distributed storage system**.

The core story is:

> A user uploads data once. REFORGE places multiple copies across independent laptops. If a laptop dies, becomes unreachable, or contains corrupted data, REFORGE detects the problem and automatically restores the required protection without the user needing to do anything.

The judge should be able to see this happen live.

---

# 2. Our Four-Laptop Setup

We have four physical laptops.

Each laptop acts as an independent storage node.

```text
                    USER / JUDGE
                         |
                         |
                    REFORGE UI
                         |
                         v
              +----------------------+
              | Gateway / Coordinator |
              |      Laptop 1         |
              +----------+-----------+
                         |
             +-----------+-----------+
             |           |           |
             v           v           v
          Laptop 1    Laptop 2    Laptop 3    Laptop 4
           Node 1      Node 2      Node 3      Node 4
             |           |           |           |
             v           v           v           v
           SSD/HDD      SSD/HDD     SSD/HDD     SSD/HDD
```

### Important

Laptop 1 has two roles:

1. Storage Node 1
2. Gateway / Coordinator / Dashboard host

Laptops 2, 3 and 4 are storage nodes.

This is a **hackathon topology**. It is not claiming that the coordinator is itself fully fault tolerant.

The important distributed behavior being demonstrated is the behavior of the **storage nodes and their data replicas**.

---

# 3. How the Laptops Are Connected

All four laptops should be on the same local network.

Example:

```text
Laptop 1 → 192.168.1.101
Laptop 2 → 192.168.1.102
Laptop 3 → 192.168.1.103
Laptop 4 → 192.168.1.104
```

Actual addresses will depend on the network.

Each laptop runs a REFORGE storage-node process.

The node exposes an internal API, for example:

```text
Laptop 1 → :7001
Laptop 2 → :7002
Laptop 3 → :7003
Laptop 4 → :7004
```

The Gateway can talk to all four nodes over the local network.

---

# 4. What Is Actually Storing the Data?

The laptops' **own SSD/HDD storage** is the storage.

We are NOT using:

- AWS S3 as the actual storage layer
- Google Drive
- Firebase Storage
- Supabase Storage
- another cloud-storage provider

Those services would hide the exact storage/recovery problem we are trying to demonstrate.

Each laptop has its own storage directory.

Example:

```text
Laptop 1
D:\reforge-data\node1\objects\

Laptop 2
D:\reforge-data\node2\objects\

Laptop 3
D:\reforge-data\node3\objects\

Laptop 4
D:\reforge-data\node4\objects\
```

The application treats these as four independent storage machines.

---

# 5. Replication Policy for the Demo

Use:

```text
Replication Factor = 3
```

Meaning:

> Every important demo object must have three valid replicas on three different laptops.

Example:

```text
photo.jpg

Node 1 → ✅
Node 2 → ✅
Node 3 → ✅
Node 4 → spare
```

The user sees only:

```text
photo.jpg
```

They do NOT need to know which laptops physically contain it.

---

# 6. What the User Thinks Is Happening

From the user's perspective:

```text
Upload photo.jpg
        |
        v
   REFORGE STORAGE
        |
        v
    Upload complete
```

They do not manually choose:

```text
Node 1
Node 2
Node 3
```

REFORGE decides that automatically.

That is the point of the system.

---

# 7. What Is Actually Happening During Upload

Suppose the user uploads:

```text
dataset.zip
```

The flow is:

```text
User
  |
  | PUT dataset.zip
  v
Gateway
  |
  | calculate metadata / checksum
  v
Placement Engine
  |
  | choose N1, N2, N3
  |
  +------> Node 1
  |
  +------> Node 2
  |
  +------> Node 3
```

The result becomes:

```text
Node 1 → dataset.zip ✅
Node 2 → dataset.zip ✅
Node 3 → dataset.zip ✅
Node 4 → unused for this object's initial placement
```

The metadata should record:

```text
Object: dataset.zip
Version: 1
Checksum: <SHA-256>
Size: <bytes>
Replicas: [Node 1, Node 2, Node 3]
Replication Factor: 3
```

---

# 8. The Main Demo Dashboard

The dashboard should make the system understandable in seconds.

Recommended layout:

```text
+------------------------------------------------------+
|                    REFORGE                           |
|        Autonomous Self-Healing Object Storage       |
+------------------------------------------------------+

CLUSTER STATUS
--------------------------------------------------------
Node 1     🟢 HEALTHY      42 GB used
Node 2     🟢 HEALTHY      38 GB used
Node 3     🟢 HEALTHY      44 GB used
Node 4     🟢 HEALTHY      31 GB used

Replication Factor: 3
Objects: 1,284
Protected Objects: 1,284
Repair Queue: 0

--------------------------------------------------------

OBJECT: dataset.zip

Version: 1
Checksum: SHA-256: abc123...
Required replicas: 3

Node 1     ✅
Node 2     ✅
Node 3     ✅
Node 4     -

--------------------------------------------------------

LIVE EVENTS
[12:01:20] Object uploaded
[12:01:21] Replicas created on Node 1, 2, 3
[12:01:21] Integrity verified
```

The dashboard should update in real time.

---

# 9. DEMO SCENARIO 1 — NORMAL OPERATION

## Goal

First prove that the system works normally before creating failures.

### Step 1

Show four healthy laptops:

```text
N1 🟢
N2 🟢
N3 🟢
N4 🟢
```

### Step 2

Upload a visible demo file.

Good examples:

```text
dataset.zip
demo-video.mp4
large-image.zip
sample-database.zip
```

For a live hackathon, use a file large enough to make the distributed behavior visible but small enough to upload quickly.

Example:

```text
100–500 MB
```

### Step 3

Show the replica map:

```text
dataset.zip

N1 ✅
N2 ✅
N3 ✅
N4 -
```

### Step 4

Show the same checksum on all healthy replicas.

Example:

```text
N1 → SHA-256 = ABC123
N2 → SHA-256 = ABC123
N3 → SHA-256 = ABC123
```

This establishes the baseline.

---

# 10. DEMO SCENARIO 2 — KILL ONE LAPTOP

This should be the **main wow moment**.

Initial state:

```text
dataset.zip

N1 ✅
N2 ✅
N3 ✅
N4 -
```

Now physically stop the Node 2 process.

For example:

```text
Node 2 process → STOP
```

Or shut down Laptop 2 if the environment allows it.

### What should happen

The other nodes continue operating.

The dashboard should show:

```text
N1 🟢
N2 🟠 SUSPECTED
N3 🟢
N4 🟢
```

Then:

```text
N2 🔴 OFFLINE
```

The system calculates:

```text
Required replicas = 3
Available replicas = 2
Replica deficit = 1
```

Then the repair controller selects Node 4.

```text
N1 ✅
N2 ❌
N3 ✅
N4 🔄 REPAIRING
```

Node 4 receives the missing replica.

Then checksum verification runs:

```text
N4 → SHA-256 = ABC123
```

Finally:

```text
N1 ✅
N2 ❌
N3 ✅
N4 ✅
```

Dashboard:

```text
DURABILITY RESTORED ✅

Required replicas: 3
Healthy replicas: 3
```

---

# 11. What the Judge Should Hear During Scenario 2

The explanation should be simple:

> "The object originally had three replicas. We are now killing one of the storage nodes. REFORGE detects the node failure, determines which objects became under-replicated, selects another healthy node, rebuilds the missing replica, verifies its checksum, and restores the required replication level."

Do NOT spend the demo explaining every implementation detail.

Show the system doing the work.

---

# 12. DEMO SCENARIO 3 — CORRUPT A REPLICA

This demonstrates that:

> A node being alive does not mean its data is correct.

Start with:

```text
N1 ✅
N3 ✅
N4 ✅

dataset.zip checksum:
ABC123
```

Now intentionally corrupt the copy on Node 3.

For the demo, this can be done with a controlled test action rather than manually editing files.

For example:

```text
[ CORRUPT OBJECT ON NODE 3 ]
```

The button calls a development/test endpoint that flips or modifies bytes in the selected replica.

Now:

```text
N1 → ABC123
N3 → XYZ999  ❌
N4 → ABC123
```

The node itself is still online:

```text
N3 🟢 reachable
```

But the integrity check says:

```text
CHECKSUM MISMATCH
```

REFORGE should then:

```text
detect corruption
      ↓
mark replica unhealthy / quarantine it
      ↓
select healthy source replica
      ↓
rebuild Node 3 copy
      ↓
verify checksum
      ↓
mark replica healthy
```

Final state:

```text
N1 → ABC123 ✅
N3 → ABC123 ✅
N4 → ABC123 ✅
```

This is a very strong part of the demo because the failure is not simply "the laptop died."

---

# 13. DEMO SCENARIO 4 — NETWORK PARTITION

This demonstrates the difference between:

```text
Node failure
```

and:

```text
Node is alive but unreachable
```

Example:

```text
N1 ✅
N2 ✅
N3 ✅
N4 ✅
```

Create a temporary network block between the coordinator and Node 3.

Conceptually:

```text
Coordinator
     |
     X
     |
    N3
```

Node 3 itself is still running.

Its local process may say:

```text
"I am alive."
```

But the coordinator cannot reach it.

REFORGE should show:

```text
N3 → SUSPECTED
```

before deciding it is unavailable according to the configured timeout/probe policy.

Do NOT claim:

> "Network timeout proves the machine has physically failed."

Instead say:

> "From the coordinator's point of view, Node 3 is currently unreachable, so REFORGE treats it as suspected/unavailable and protects the affected replicas according to policy."

This is technically more accurate.

---

# 14. DEMO SCENARIO 5 — NODE COMES BACK

After Node 2 or Node 3 has been offline, bring it back.

Important:

Do NOT simply mark it healthy immediately.

It should enter:

```text
RECOVERING
```

Example:

```text
Node 2 🔵 RECOVERING

Checking:
- node identity
- epoch / lease
- local objects
- object versions
- checksums
```

Then:

```text
stale data → replaced
missing data → restored
valid data → retained
```

After synchronization:

```text
Node 2 🟢 HEALTHY
```

This shows that the system can handle recovery, not just failure.

---

# 15. DEMO SCENARIO 6 — BACKGROUND REBALANCING

This is optional if time permits.

Suppose:

```text
N1 → 60 GB
N2 → 55 GB
N3 → 15 GB
N4 → 10 GB
```

Now introduce or enable a new node capacity.

The rebalancer gradually moves eligible replicas/chunks to reduce imbalance.

Show:

```text
BEFORE

N1 ██████████
N2 █████████
N3 ███
N4 ██

AFTER

N1 ███████
N2 ███████
N3 ██████
N4 █████
```

The key rule:

> Never delete an old replica until the new replica has been successfully copied and verified.

---

# 16. DEMO SCENARIO 7 — LARGE OBJECT / CHUNKING

If the system implements chunking, demonstrate it.

Example:

```text
demo-video.mp4 = 500 MB
```

REFORGE splits it:

```text
Chunk 0
Chunk 1
Chunk 2
...
Chunk N
```

Instead of treating the object as one giant indivisible file.

The dashboard can show:

```text
demo-video.mp4

Chunks: 10
Replication: 3×

Chunk 0 → N1 N2 N4
Chunk 1 → N1 N3 N4
Chunk 2 → N2 N3 N4
...
```

This makes the design look more scalable.

This part is optional for the first working demo.

---

# 17. Recommended Demo Order

Do NOT randomly demonstrate features.

Use this exact story:

```text
1. Show 4 healthy laptops
        ↓
2. Upload one object
        ↓
3. Show 3 replicas on 3 laptops
        ↓
4. Show identical checksums
        ↓
5. Kill one node
        ↓
6. Show failure detection
        ↓
7. Show replica deficit
        ↓
8. Show automatic repair to fourth node
        ↓
9. Verify checksum
        ↓
10. Corrupt another replica
        ↓
11. Detect checksum mismatch
        ↓
12. Automatically repair corruption
        ↓
13. Optional: demonstrate network partition
        ↓
14. Optional: restart failed node
        ↓
15. Show it recovering and synchronizing
```

This produces a complete story:

```text
STORE
  ↓
FAIL
  ↓
DETECT
  ↓
REPAIR
  ↓
VERIFY
  ↓
RECOVER
```

---

# 18. What Should Be Visible on the Dashboard

At minimum:

## Cluster

```text
Node ID
Status
Last heartbeat
Health
Free storage
Latency
```

## Object

```text
Object name
Version
Size
Checksum
Required replica count
Current healthy replica count
Replica locations
```

## Recovery

```text
Failed node
Affected objects
Repair progress
Source node
Target node
Bytes copied
Checksum result
Recovery time
```

## Live event log

Example:

```text
12:01:20  PUT dataset.zip
12:01:21  Replicated to Node 1
12:01:21  Replicated to Node 2
12:01:22  Replicated to Node 3
12:01:22  Integrity verified

12:03:10  Node 2 missed heartbeat
12:03:12  Node 2 marked SUSPECTED
12:03:15  Node 2 marked OFFLINE
12:03:15  Replica deficit detected
12:03:16  Repair scheduled → Node 4
12:03:19  Replica copied
12:03:20  Checksum verified
12:03:20  DURABILITY RESTORED
```

This event timeline is very valuable for judges.

---

# 19. Physical Demo Setup

The best physical arrangement is:

```text
             BIG SCREEN / PROJECTOR
                    |
                    v
              REFORGE DASHBOARD

   Laptop 1          Laptop 2
   Node 1            Node 2
   Coordinator

   Laptop 3          Laptop 4
   Node 3            Node 4
```

Each laptop should have a visible label:

```text
┌───────────────┐
│  NODE 1 🟢    │
└───────────────┘

┌───────────────┐
│  NODE 2 🟢    │
└───────────────┘

┌───────────────┐
│  NODE 3 🟢    │
└───────────────┘

┌───────────────┐
│  NODE 4 🟢    │
└───────────────┘
```

When a node fails, physically showing:

```text
NODE 2 🔴 OFFLINE
```

makes the demo much easier to understand.

---

# 20. How We Should Kill a Node During the Demo

Prefer a controlled software failure.

Example:

```text
[ KILL NODE 2 ]
```

This stops only the Node 2 service.

Advantages:

- repeatable
- safe
- fast
- no need to shut down the entire laptop
- easy to demonstrate several times

A physical laptop shutdown can be a backup demonstration.

---

# 21. How We Should Simulate Corruption

Do not randomly edit the storage files by hand during the live demo.

Instead create a test-only action:

```text
[ CORRUPT REPLICA ]
```

The system intentionally changes a few bytes in the selected stored object.

Then the normal integrity verification machinery detects it.

This is cleaner and repeatable.

---

# 22. How We Should Simulate a Network Partition

Use a test control that temporarily blocks the Node 3 network connection/port from the coordinator.

Possible implementation methods:

- firewall rule
- controlled network block
- test proxy
- service-level communication block

The important thing is that:

```text
Node process stays running
but coordinator cannot reach it.
```

That creates the network-partition demonstration.

---

# 23. Important: The Demo Must Not Rely on Cloud Storage

Do not make the core flow:

```text
User
 ↓
REFORGE
 ↓
AWS S3
```

That would undermine the main point of the project.

The actual object bytes used in the demo should be stored on:

```text
Laptop 1 SSD
Laptop 2 SSD
Laptop 3 SSD
Laptop 4 SSD
```

The laptops ARE the storage cluster.

---

# 24. What We Are Actually Demonstrating

The judges should walk away understanding these five things:

### 1. Distribution

The same logical object is stored across different physical machines.

### 2. Fault tolerance

One machine can disappear without making the protected object unavailable.

### 3. Integrity

A machine can be alive while its data is corrupted, and REFORGE can detect that using checksums.

### 4. Self-healing

REFORGE automatically restores the configured replica count.

### 5. Recovery

When a failed node returns, REFORGE can reconcile and synchronize it rather than blindly trusting it.

---

# 25. The One-Sentence Explanation for Teammates

Use this when explaining the demo internally:

> "We have four real laptops acting as four storage nodes; the user uploads one object to REFORGE, the system stores three replicas across three laptops, and then we intentionally kill, isolate, or corrupt a node to show REFORGE detecting the problem and automatically rebuilding a correct replica on another laptop."

---

# 26. The One-Sentence Explanation for Judges

> "REFORGE turns four unreliable independent machines into one logical object-storage system that automatically detects failures, verifies data integrity, and restores durability without manual intervention."

---

# 27. Minimum Demo That MUST Work

If development time becomes short, forget the advanced features temporarily.

The minimum winning demo should be:

```text
4 physical laptops
      ↓
4 storage nodes
      ↓
Upload object
      ↓
3 replicas created
      ↓
Kill one node
      ↓
Failure detected
      ↓
Replica deficit detected
      ↓
Fourth node selected
      ↓
Replica copied
      ↓
Checksum verified
      ↓
3 healthy replicas restored
```

That end-to-end loop must be rock solid.

Everything else can be layered on afterward.

---

# 28. Advanced Features Should Be Added After the Core Demo Works

Priority order:

```text
P0 — MUST HAVE
- 4-node cluster
- object upload
- replication
- GET/download
- heartbeats
- failure detection
- automatic repair
- checksum verification

P1 — VERY VALUABLE
- node recovery
- leases / epochs
- corruption injection
- network partition simulation
- repair progress dashboard

P2 — DIFFERENTIATORS
- failure-domain-aware placement
- adaptive replication
- proactive replica migration
- repair throttling
- background rebalancing
- chunking / parallel transfer
- predictive health/risk scoring
```

---

# 29. Final Demo Architecture

```text
                         USER
                           |
                           v
                  +----------------+
                  | REFORGE GATEWAY|
                  |   Laptop 1     |
                  +-------+--------+
                          |
                     METADATA
                     PLACEMENT
                     HEALTH
                          |
          +---------------+---------------+
          |               |               |
          v               v               v
      +-------+       +-------+       +-------+
      |Node 1 |       |Node 2 |       |Node 3 |
      |Laptop1|       |Laptop2|       |Laptop3|
      +-------+       +-------+       +-------+
          \               |               /
           \              |              /
            \             |             /
                    +------------+
                    |   Node 4   |
                    |  Laptop 4  |
                    +------------+

                         |
                         v
                 SELF-HEAL ENGINE
                         |
          +--------------+--------------+
          |              |              |
       Detect         Repair         Verify
       Failure        Replica        Checksum
```

---

# 30. Final Mental Model

Remember this:

```text
             FOUR LAPTOPS
                  |
                  v
          FOUR STORAGE NODES
                  |
                  v
          ONE LOGICAL VAULT
                  |
                  v
        DATA STORED IN MULTIPLE
        PHYSICAL LOCATIONS
                  |
           something breaks
                  |
                  v
              DETECT
                  |
                  v
             UNDER-PROTECTED?
                  |
             YES /      \ NO
                 /        \
                v          v
             REPAIR      CONTINUE
                |
                v
             VERIFY
                |
                v
        PROTECTION RESTORED
```

That is the entire live-demo concept.

## The demo is successful when the audience can literally watch:

```text
3 replicas
   ↓
Node failure
   ↓
2 replicas
   ↓
Automatic repair
   ↓
3 replicas again
   ↓
Checksum = correct
```

That is the moment that proves the project.
