// App-wide switches for testing builds.

/**
 * TEST MODE (on for now). Sign-in accepts any details: no field is required
 * and there is no OTP step, so testers can reach every workspace with a
 * random name or number. Turn this off when the real SMS / account backend
 * is connected; the OTP screen and validation in auth.tsx come back as is.
 */
export const AUTH_TEST_MODE = true;

// Care Circle contacts now come from the care network (her zone's ASHA and
// her PHC's doctors): see data/care-network.ts. DEMO numbers until profiles
// come from the backend; 108 is the real national ambulance number.
