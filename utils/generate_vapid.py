"""
VAPID Key Generator for PWA Push Notifications.
Run once: python utils/generate_vapid.py
Then paste the output into your .env file.
"""

def generate_vapid_keys():
    try:
        from py_vapid import Vapid
        vapid = Vapid()
        vapid.generate_keys()
        private_key = vapid.private_pem().decode("utf-8").replace("\n", "\\n")
        public_key  = vapid.public_key.public_bytes(
            encoding=__import__("cryptography.hazmat.primitives.serialization",
                               fromlist=["Encoding"]).Encoding.PEM,
            format=__import__("cryptography.hazmat.primitives.serialization",
                              fromlist=["PublicFormat"]).PublicFormat.SubjectPublicKeyInfo
        ).decode("utf-8")

        from py_vapid import b64urlencode
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        raw_public = vapid.public_key.public_bytes(Encoding.X962,
                                                    PublicFormat.UncompressedPoint)
        public_key_b64 = b64urlencode(raw_public)

        print("=" * 60)
        print("VAPID Keys generated — add to your .env file:")
        print("=" * 60)
        print(f"\nVAPID_PUBLIC_KEY={public_key_b64}")
        print(f"\nVAPID_PRIVATE_KEY={vapid.private_pem().decode().strip()}")
        print("\nAlso add VAPID_CLAIMS_EMAIL=admin@yourdomain.com")
        print("=" * 60)

    except ImportError:
        try:
            # Alternative using pywebpush directly
            from pywebpush import Vapid
            v = Vapid()
            v.generate_keys()
            print("=" * 60)
            print("VAPID Keys — add to .env:")
            print("=" * 60)
            v.save_key("vapid_private.pem")
            v.save_public_key("vapid_public.pem")
            print("Keys saved to vapid_private.pem and vapid_public.pem")
            print("Add their content to your .env as VAPID_PRIVATE_KEY and VAPID_PUBLIC_KEY")
            print("=" * 60)
        except ImportError:
            print("Install pywebpush first: pip install pywebpush")
            print("Then run: python utils/generate_vapid.py")


if __name__ == "__main__":
    generate_vapid_keys()
