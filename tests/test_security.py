from config.security import hash_password, verify_password


def test_bcrypt_hashing_and_verification_work_for_registration_passwords():
    password = "AfmSandboxRegistrationPassword2026!"

    hashed = hash_password(password)

    assert hashed != password
    assert verify_password(password, hashed)

