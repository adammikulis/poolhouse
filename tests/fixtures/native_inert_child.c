/* Harmless plumbing fixture: even an unexpected resume only writes one byte. */
#include <unistd.h>
int main(void) {
    const char marker = 'x';
    (void)write(3, &marker, 1);
    return 0;
}
