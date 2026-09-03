// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title AttestationRegistry
/// @notice Tamper-evident record of a face-match run.
///
/// What is deliberately NOT stored: images, URLs, embeddings, or any identifier of the
/// person matched. Only a Merkle root over the run's evidence goes on chain. The ledger is
/// public and permanent, so anything written here is written forever - a commitment proves
/// the run happened and has not been altered without publishing who it was about.
///
/// Two things a face-matching registry cannot honestly ship without:
///
///   consent      A record that the subject authorised the scan, bound into the
///                attestation itself. Stored as a hash, so the consent document stays
///                off-chain while the fact of it is permanent and checkable.
///   revocation   A match can turn out to be wrong, or a subject can withdraw. Chain
///                history cannot be erased, so an attestation is instead marked revoked
///                and every reader learns it should no longer be relied on.
///
/// A single hash would prove only "something changed". A root additionally lets one field
/// be disclosed and proved on its own via verifyInclusion, leaving the rest undisclosed.
contract AttestationRegistry {
    struct Attestation {
        uint64 timestamp;
        uint32 candidateCount;
        bool matched;
        address submitter;
        /// @dev keccak256 of the subject's signed consent record. Zero means the run
        /// recorded no consent - permitted, but readers can and should treat it
        /// differently from an authorised scan.
        bytes32 consentHash;
        /// @dev 0 while live. Non-zero is the block timestamp it was withdrawn.
        uint64 revokedAt;
        bytes32 revocationReason;
    }

    mapping(bytes32 => Attestation) private _attestations;

    bytes32[] private _roots;

    event MatchRecorded(
        bytes32 indexed root,
        address indexed submitter,
        uint64 timestamp,
        uint32 candidateCount,
        bool matched,
        bytes32 consentHash
    );

    event AttestationRevoked(
        bytes32 indexed root,
        address indexed revokedBy,
        uint64 timestamp,
        bytes32 reason
    );

    error AlreadyRecorded(bytes32 root);
    error EmptyRoot();
    error UnknownRoot(bytes32 root);
    error AlreadyRevoked(bytes32 root);
    error NotSubmitter(bytes32 root, address caller);

    /// @notice Commit one run's evidence root.
    /// @param consentHash keccak256 of the subject's signed consent record, or zero.
    function record(
        bytes32 root,
        uint32 candidateCount,
        bool matched,
        bytes32 consentHash
    ) external {
        if (root == bytes32(0)) revert EmptyRoot();
        if (_attestations[root].timestamp != 0) revert AlreadyRecorded(root);

        _attestations[root] = Attestation({
            timestamp: uint64(block.timestamp),
            candidateCount: candidateCount,
            matched: matched,
            submitter: msg.sender,
            consentHash: consentHash,
            revokedAt: 0,
            revocationReason: bytes32(0)
        });
        _roots.push(root);

        emit MatchRecorded(
            root, msg.sender, uint64(block.timestamp), candidateCount, matched, consentHash
        );
    }

    /// @notice Withdraw an attestation. The record stays - chain history cannot be
    /// erased - but every reader now learns it should not be relied on.
    /// @dev Only the original submitter. A registry where anyone can revoke anyone's
    /// record is worse than one with no revocation at all.
    function revoke(bytes32 root, bytes32 reason) external {
        Attestation storage a = _attestations[root];
        if (a.timestamp == 0) revert UnknownRoot(root);
        if (a.submitter != msg.sender) revert NotSubmitter(root, msg.sender);
        if (a.revokedAt != 0) revert AlreadyRevoked(root);

        a.revokedAt = uint64(block.timestamp);
        a.revocationReason = reason;

        emit AttestationRevoked(root, msg.sender, uint64(block.timestamp), reason);
    }

    /// @notice Read a stored attestation. Reverts if the root was never recorded, so an
    /// absent record is distinguishable from a zeroed one.
    function get(bytes32 root)
        external
        view
        returns (
            uint64 timestamp,
            uint32 candidateCount,
            bool matched,
            address submitter,
            bytes32 consentHash,
            uint64 revokedAt,
            bytes32 revocationReason
        )
    {
        Attestation memory a = _attestations[root];
        if (a.timestamp == 0) revert UnknownRoot(root);
        return (
            a.timestamp,
            a.candidateCount,
            a.matched,
            a.submitter,
            a.consentHash,
            a.revokedAt,
            a.revocationReason
        );
    }

    function exists(bytes32 root) external view returns (bool) {
        return _attestations[root].timestamp != 0;
    }

    /// @notice Recorded and not withdrawn. This is the question a consumer should ask,
    /// rather than `exists`, which stays true forever once written.
    function isLive(bytes32 root) external view returns (bool) {
        Attestation memory a = _attestations[root];
        return a.timestamp != 0 && a.revokedAt == 0;
    }

    function hasConsent(bytes32 root) external view returns (bool) {
        return _attestations[root].consentHash != bytes32(0);
    }

    function count() external view returns (uint256) {
        return _roots.length;
    }

    function rootAt(uint256 index) external view returns (bytes32) {
        return _roots[index];
    }

    /// @notice Prove one leaf belongs to a recorded root without revealing the others.
    /// @dev Sorted-pair hashing, matching OpenZeppelin's MerkleProof, so proofs need no
    /// left/right flags. Inlined rather than imported to keep the repo npm-light.
    function verifyInclusion(bytes32 root, bytes32 leaf, bytes32[] calldata proof)
        external
        view
        returns (bool)
    {
        if (_attestations[root].timestamp == 0) revert UnknownRoot(root);

        bytes32 node = leaf;
        for (uint256 i = 0; i < proof.length; ++i) {
            bytes32 sibling = proof[i];
            node = node <= sibling
                ? keccak256(abi.encodePacked(node, sibling))
                : keccak256(abi.encodePacked(sibling, node));
        }
        return node == root;
    }
}
