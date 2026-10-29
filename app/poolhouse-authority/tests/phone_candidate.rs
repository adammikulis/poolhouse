use poolhouse_authority::phone_candidate::verify;
use ring::signature::KeyPair;

#[test]
fn candidate_requires_exact_canonical_proof_and_pinned_node() {
    let transcript = b"poolhouse/phone-add/v2\n{\"alg\":\"p256\",\"at_ms\":1790000000000,\"auth\":\"05305932a1c7eabe13b6cec9fda48882\",\"expires\":1790000300,\"key\":\"048e533b6fa0bf7b4625bb30667c01fb607ef9f8b8a80fef5b300628703187b2a373eb1dbde03318366d069f83a6f5900053c73633cb041b21c55e1a86c1f400b4\",\"name\":\"Pixel 9\",\"node\":\"3a05903849a971ff4d44654f2497d8f8a593691f56ca7af1c50c55bd03f5f0ba\",\"offer\":\"66687aadf862bd776c8fc18b8e9f8e20089714856ee233b3902a591d0d5f2925\",\"op\":\"phone_add\",\"push\":\"042571b2becdfde360551aaf1ed0f4cd366c11cebe555f89bcb7b186a53339173168ece2ebe018597bd30479b86e3c8f8eced577ca59187e9246990db682008b0e\",\"tls\":\"7777777777777777777777777777777777777777777777777777777777777777\"}";
    let signature = "3045022100dbaf83ac5c22db9644229f455d054acae08d42f20ac29fde72f23f1eefe249c00220216ae6f222be85377ec450b7b4f20cc955c304aa6c19fde18419bb29f744d50f";
    let node = "3a05903849a971ff4d44654f2497d8f8a593691f56ca7af1c50c55bd03f5f0ba";
    let tls = "7777777777777777777777777777777777777777777777777777777777777777";
    assert!(verify(transcript, signature, node, tls).is_ok());
    assert!(verify(transcript, signature, tls, tls).is_err());
    for index in 0..transcript.len() {
        let mut changed = transcript.to_vec();
        changed[index] ^= 1;
        assert!(verify(&changed, signature, node, tls).is_err(), "changed byte {index}");
    }
    assert!(verify(transcript, &signature.to_uppercase(), node, tls).is_err());
    assert!(verify(transcript, &signature[..signature.len()-2], node, tls).is_err());
    assert!(verify(transcript, &"00".repeat(73), node, tls).is_err());

    // A valid signature over duplicate fields must still be refused by the parser.
    let random = ring::rand::SystemRandom::new();
    let algorithm = &ring::signature::ECDSA_P256_SHA256_ASN1_SIGNING;
    let pkcs8 = ring::signature::EcdsaKeyPair::generate_pkcs8(algorithm, &random).unwrap();
    let key = ring::signature::EcdsaKeyPair::from_pkcs8(algorithm, pkcs8.as_ref(), &random).unwrap();
    let public: String = key.public_key().as_ref().iter().map(|b| format!("{b:02x}")).collect();
    let text = std::str::from_utf8(transcript).unwrap();
    let original_key = "048e533b6fa0bf7b4625bb30667c01fb607ef9f8b8a80fef5b300628703187b2a373eb1dbde03318366d069f83a6f5900053c73633cb041b21c55e1a86c1f400b4";
    let canonical = text.replace(original_key, &public);
    let duplicate = canonical.replace("\"op\":\"phone_add\"", "\"op\":\"phone_add\",\"op\":\"phone_add\"");
    let sign = |text: &str| loop {
        let sig = key.sign(&random, text.as_bytes()).unwrap();
        if poolhouse_authority::phone_candidate::canonical_p256(sig.as_ref()) {
            break sig.as_ref().iter().map(|b| format!("{b:02x}")).collect::<String>();
        }
    };
    assert!(verify(canonical.as_bytes(), &sign(&canonical), node, tls).is_ok());
    assert!(verify(duplicate.as_bytes(), &sign(&duplicate), node, tls).is_err());
}
