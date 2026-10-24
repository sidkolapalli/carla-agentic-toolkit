# Managed runtime invariants

The trusted experiment worker accepts a finite `ExperimentSpec`; generated Python
runs only in the Rust/Landlock sandbox. Persistent script sessions reuse that
sandboxed namespace. These are separate modes with a shared cooperative simulator
lease. Provider credentials and provider networking belong only to trusted code.

| Responsibility | Owner and invariant |
| --- | --- |
| Mutation ownership | `SimulatorLease` serializes cooperating processes at one configured endpoint. Every process must share private state and an unambiguous address. Unrelated CARLA clients are outside this coordination. |
| World clock | `ManagedSession` retains the connection and owns scheduled ticks. Setup frames are recorded separately; unexpected frame/episode changes invalidate the run. Fixture, planner, sensors, and provider never tick. |
| Actor lifecycle | Creation ownership, assigned control, protection and descriptive roles are separate. Managed vehicles, sensors and demo cameras persist an episode-bound intent before spawning and the returned ID before setup. Unknown replies remain quarantined; role or inventory matches never establish ownership. Direct returned handles survive journal failures. Each controlled vehicle has one tracker. |
| Script journals | Finite and persistent scripts bind actor IDs to a world episode lazily before creation. Unknown legacy identities cannot authorize cleanup; old-episode IDs never authorize destruction in a replacement world. Uncached destruction uses an authoritative non-ticking server response. |
| Observations | One world snapshot provides frame/time and range-filtered simulator ground truth. Numerical geometry stays in code; this mode does not claim realistic occlusion or hidden intentions. |
| Sensors | Listeners attach before owner ticks; bounded queues drain by measurement frame. Event absence is normal, periodic gaps are explicit, and final stop/drain preserves trailing evidence. |
| Decisions | Pure code generates bounded candidates and fallback choices. Every reply is revalidated against run/world/actor, frame, candidate set, phase, generation, deadline, and current applicability. |
| Provider I/O | The optional Jev adapter owns one retained async client, bounded attempts/deadlines, and shared persistent reservations. Unknown actual usage remains unknown. |
| Cancellation | The supervisor requests stop, waits a bounded grace, then terminates the complete worker group if required. Status distinguishes the request, actual termination, recovery, and verified cleanup. |
| Recovery | A confirmed dead worker's dirty lease and journals remain until trusted recovery verifies cleanup. A failed recovery blocks another cooperating mutator rather than silently clearing ownership. |
| Evidence | Only trusted code appends private, bounded, versioned JSONL events. Traces retain original numerical data, requested decisions, executed controls, sensor delivery, and outcomes. Static reports derive only from saved evidence. |

The planner and tracker share deterministic numerical contracts across rules and
Jev policies. Jev selects candidate IDs; it does not supply trajectories, controls,
provider URLs, or executable code. Controller interventions remain attributable to
the controller. Recorded-response replay requires exact saved identities and must
not be confused with applying fixed actions to a new scene.

See [managed experiments](managed-experiments.md) for lifecycle and limits,
[persistent sessions](persistent-sessions.md) for retained generated code,
[sensor timing](sensor-timing.md) for non-ticking APIs, and
[evidence and comparison](experiment-evidence.md) for report definitions.
