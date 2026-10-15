package app.poolhouse.phone;

import java.util.Arrays;

final class PoolKeyEnrollmentTest {
    interface Operation { void run() throws Exception; }
    private static int checks;
    private static void check(boolean value) { if (!value) throw new AssertionError(); checks++; }
    private static void refuse(Operation operation) throws Exception {
        try { operation.run(); } catch (SecurityException expected) { checks++; return; }
        throw new AssertionError("Enrollment accepted a different binding");
    }
    private static PoolKeyEnrollment binding(String[] fields, long expires, long now) throws Exception {
        return PoolKeyEnrollment.checked(fields[0], fields[1], fields[2], fields[3], fields[4], expires, now,
                                         fields[0], fields[1], fields[2]);
    }
    public static void main(String[] args) throws Exception {
        String[] fields = {"1".repeat(64), "2".repeat(64), "3".repeat(64), "4".repeat(64), "5".repeat(64)};
        PoolKeyEnrollment original = binding(fields, 1300, 1000);
        check(original.binding().equals("2df12fb374732b8b7b328e490d97a67a06c5d41615b80ba6927719118bb17bd9"));
        for (int index = 0; index < fields.length; index++) {
            String[] changed = fields.clone(); changed[index] = "a".repeat(64);
            check(!Arrays.equals(original.challenge(), binding(changed, 1300, 1000).challenge()));
        }
        check(!Arrays.equals(original.challenge(), binding(fields, 1299, 1000).challenge()));
        byte[] exported = original.challenge(); exported[0] ^= 1;
        check(!Arrays.equals(exported, original.challenge()));
        for (int index = 0; index < 3; index++) {
            String[] expected = fields.clone(); expected[index] = "a".repeat(64);
            refuse(() -> PoolKeyEnrollment.checked(fields[0], fields[1], fields[2], fields[3], fields[4], 1300, 1000,
                                                   expected[0], expected[1], expected[2]));
        }
        for (String bad : new String[]{null, "", "A".repeat(64), "0".repeat(64), "ab", "../alias"}) {
            for (int index = 0; index < fields.length; index++) {
                String[] changed = fields.clone(); changed[index] = bad;
                refuse(() -> binding(changed, 1300, 1000));
            }
        }
        for (long expiry : new long[]{-1, 999, 1000, 1301, Long.MAX_VALUE}) refuse(() -> binding(fields, expiry, 1000));
        refuse(() -> binding(fields, 1300, -1));
        original.requireCurrent(1299);
        refuse(() -> original.requireCurrent(1300));
        refuse(() -> original.requireCurrent(999));
        byte[] point = new byte[65]; point[0] = 4; point[64] = 1;
        byte[] consent = original.consent(point, "6".repeat(64));
        check(!Arrays.equals(consent, original.challenge()));
        byte[] anotherPoint = point.clone(); anotherPoint[64] = 2;
        check(!Arrays.equals(consent, original.consent(anotherPoint, "6".repeat(64))));
        check(!Arrays.equals(consent, original.consent(point, "7".repeat(64))));
        refuse(() -> original.consent(new byte[65], "6".repeat(64)));
        refuse(() -> original.consent(point, "0".repeat(64)));
        PoolKeyEnrollment.Allocation reserved = PoolKeyEnrollment.Allocation.reserve("ab".repeat(16), original);
        reserved.require(original);
        check(PoolKeyEnrollment.Allocation.parse(reserved.encoded()).encoded().equals(reserved.encoded()));
        PoolKeyEnrollment.Allocation disclosed = reserved.disclose();
        disclosed.require(original);
        refuse(() -> disclosed.abandon());
        PoolKeyEnrollment.Allocation abandoning = reserved.abandon();
        check(abandoning.abandoning());
        refuse(() -> abandoning.disclose());
        refuse(() -> abandoning.require(original));
        refuse(() -> abandoning.abandon());
        String[] changed = fields.clone(); changed[3] = "b".repeat(64);
        PoolKeyEnrollment next = binding(changed, 1300, 1000);
        refuse(() -> reserved.require(next));
        for (String invalid : new String[]{"", reserved.encoded() + ":extra", "usable:" + reserved.encoded(),
                reserved.encoded().replace("ab".repeat(16), "../alias"), reserved.encoded().toUpperCase()}) {
            refuse(() -> PoolKeyEnrollment.Allocation.parse(invalid));
        }
        System.out.println(checks + " enrollment binding checks passed");
    }
}
