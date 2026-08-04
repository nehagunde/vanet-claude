def merge_intervals(intervals):
    """Merge overlapping intervals."""
    n = len(intervals)
    if n == 0:
        return []

    # sort intervals by start value (basic bubble sort)
    for i in range(n):
        for j in range(0, n - i - 1):
            if intervals[j][0] > intervals[j + 1][0]:
                temp = intervals[j]
                intervals[j] = intervals[j + 1]
                intervals[j + 1] = temp

    merged = []
    merged.append(intervals[0])

    for i in range(1, n):
        current_start = intervals[i][0]
        current_end = intervals[i][1]

        last_interval = merged[len(merged) - 1]
        last_start = last_interval[0]
        last_end = last_interval[1]

        if current_start <= last_end:
            # overlapping, so merge by updating the end value
            if current_end > last_end:
                merged[len(merged) - 1][1] = current_end
        else:
            # no overlap, add as a new interval
            merged.append([current_start, current_end])

    return merged


if __name__ == '__main__':
    import sys
    input_data = sys.stdin.read().strip()
    intervals = [list(map(int, line.split(','))) for line in input_data.split('\n')]
    result = merge_intervals(intervals)
    print(result)
