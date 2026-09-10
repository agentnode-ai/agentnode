"""The external verification tool, second version.

The first one measured two machines by making a value on one, sending it through a job, and then
looking for it on the other. The looking was done by opening an ssh session to the far machine and
running `grep -c -- <value> /home/.../gateway.log`. A sandbox job's standard output is not written
to the gateway's log, so the answer was always `0`, and the tool reported a failure about its own
search. `EM3C-E6-RECORD-0001` found it in the sixth external run; it had been wrong in the fourth
and the fifth too, where louder failures hid it.

That tool is frozen. `agentnode_sdk.tools.external_run` refuses to start and says where to look.

What is different here is not the number of checks. It is that a channel is only ever asked what
it knows, and that what it answered is a property of the object rather than a note attached to it:

* `channels.TheGatewayItself` carries signed run records over the gateway's own transport, read by
  the production client and verified by the production client's own verification. It is the only
  place a run's output is asked about.
* `channels.TheFarMachineItself` is a shell on the far machine. It is asked what the machine IS --
  its identity, its kernel, what it is listening on. It is never asked what a run printed, because
  it does not know.

A value that arrives on one of those cannot be credited to the other: an `Answer` can only be made
by the channel that produced it, so provenance is how the object came to exist rather than a string
somebody wrote next to it.

The wire format is not restated anywhere in this package. The production serializer writes it, the
production client reads and verifies it, and a real endpoint is what the tests talk to.

Carried over from the sixth run's state, deliberately and unchanged in effect: the closed,
versioned configuration in `config`, the native start in `launcher`, and the byte-preserving
transport in `transport`. Those were what the previous arc established and what it established
them against; none of them is the thing that failed.
"""

#: This tool's own identity. Not the protocol version and not the SDK's -- what a record says it
#: was produced by, so a record from the frozen runner and a record from this one are never read
#: as though one were the other.
TOOL = "verification/2"
