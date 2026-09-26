/* Linux fixed-width types used by e2fsdroid's headers; the macOS SDK has no
 * <linux/types.h>. Same underlying types as Linux, so they coexist with
 * e2fsprogs' own ext2_types.h definitions.
 */
#ifndef MYSTIC_LINUX_TYPES_H
#define MYSTIC_LINUX_TYPES_H

typedef unsigned char __u8;
typedef signed char __s8;
typedef unsigned short __u16;
typedef signed short __s16;
typedef unsigned int __u32;
typedef signed int __s32;
typedef unsigned long long __u64;
typedef signed long long __s64;

typedef __u16 __le16;
typedef __u16 __be16;
typedef __u32 __le32;
typedef __u32 __be32;
typedef __u64 __le64;
typedef __u64 __be64;

#endif
