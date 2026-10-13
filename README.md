# The Keep release channel

Public signed release artifacts and deployment glue only. The canonical application source and household data do not belong here.

Production publication starts only from Travis's deliberate **Release The Keep** action in the private canonical repository. This repository's workflow accepts an automatically derived source run id, checks owner/main/manual-trigger identity and successful source preparation, and deploys only the enumerated verified static artifacts. Pushes do not publish.

No release has been published by initial repository configuration. Test-signing artifacts never belong on this channel.

The bounded 1301 recovery also permits the owner-triggered private **Resume 1301 publication** workflow. Its immutable validated draft receipt is checked against Actions run/step/artifact metadata and archive digests before original signed bytes deploy. The restricted source token does not read a private draft on this recovery path. Pushes still cannot publish; the private recovery retains exact HTTPS readback and same-draft finalization.
