#include <stdio.h>
#include <string.h>

int main() {
    int start[1000], end[1000];
    int n = 0;
    char line[100];

    /* read lines of "start,end" until an empty line */
    while (fgets(line, sizeof(line), stdin)) {
        if (line[0] == '\n' || line[0] == '\0') {
            break;
        }
        int s, e;
        sscanf(line, "%d,%d", &s, &e);
        start[n] = s;
        end[n] = e;
        n++;
    }

    /* sort intervals by start value (basic bubble sort) */
    for (int i = 0; i < n; i++) {
        for (int j = 0; j < n - i - 1; j++) {
            if (start[j] > start[j + 1]) {
                int temp = start[j];
                start[j] = start[j + 1];
                start[j + 1] = temp;

                temp = end[j];
                end[j] = end[j + 1];
                end[j + 1] = temp;
            }
        }
    }

    int mergedStart[1000], mergedEnd[1000];
    int mergedCount = 0;

    mergedStart[0] = start[0];
    mergedEnd[0] = end[0];
    mergedCount = 1;

    for (int i = 1; i < n; i++) {
        int currentStart = start[i];
        int currentEnd = end[i];
        int lastEnd = mergedEnd[mergedCount - 1];

        if (currentStart <= lastEnd) {
            /* overlapping, so merge by updating the end value */
            if (currentEnd > lastEnd) {
                mergedEnd[mergedCount - 1] = currentEnd;
            }
        } else {
            /* no overlap, add as a new interval */
            mergedStart[mergedCount] = currentStart;
            mergedEnd[mergedCount] = currentEnd;
            mergedCount++;
        }
    }

    printf("[");
    for (int i = 0; i < mergedCount; i++) {
        printf("[%d, %d]", mergedStart[i], mergedEnd[i]);
        if (i != mergedCount - 1) {
            printf(", ");
        }
    }
    printf("]\n");

    return 0;
}
