// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title AttestationRegistry
/// @notice Tamper-evident record of a face-match run.
///
/// What is deliberately NOT stored: images, URLs, embeddings, or any identifier of the
/// person matched. Only a Merkle root over the run's evidence goes on chain. The ledger is
/// public and permanent, so anything written here is written forever — a commitment proves
/// the run happened and has not been altered without publishing who it was about.
///
/// A single hash would prove only "something changed". A root additionally lets one field
/// be disclosed and proved on its own via verifyInclusion, leaving the rest undisclosed.
contract AttestationRegistry {
    struct Attestation {
        uint64 timestamp;
        uint32 candidateCount;
        bool matched;
        address submitter;
    }

    /// @dev Keyed by root: the commitment is the identity of the run.
    mapping(bytes32 => Attestation) private _attestations;

    bytes32[] private _roots;

    event MatchRecorded(
        bytes32 indexed root,
        address indexed submitter,
        uint64 timestamp,
        uint32 candidateCount,
        bool matched
    );

    error AlreadyRecorded(bytes32 root);
    error EmptyRoot();
    error UnknownRoot(bytes32 root);

    /// @notice Commit one run's evidence root.
    function record(bytes32 root, uint32 candidateCount, bool matched) external {
        if (root == bytes32(0)) revert EmptyRoot();
        if (_attestations[root].timestamp != 0) revert AlreadyRecorded(root);

        _attestations[root] = Attestation({
            timestamp: uint64(block.timestamp),
            candidateCount: candidateCount,
            matched: matched,
            submitter: msg.sender
        });
        _roots.push(root);

        emit MatchRecorded(root, msg.sender, uint64(block.timestamp), candidateCount, matched);
    }

    /// @notice Read a stored attestation. Reverts if the root was never recorded, so an
    /// absent record is distinguishable from a zeroed one.
    function get(bytes32 root)
        external
        view
        returns (uint64 timestamp, uint32 candidateCount, bool matched, address submitter)
    {
        Attestation memory a = _attestations[root];
        if (a.timestamp == 0) revert UnknownRoot(root);
        return (a.timestamp, a.candidateCount, a.matched, a.submitter);
    }

    function exists(bytes32 root) external view returns (bool) {
        return _attestations[root].timestamp != 0;
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
