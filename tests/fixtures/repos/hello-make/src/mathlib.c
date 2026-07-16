/* E2E fixture source. Line numbers are asserted by the gate:
 * add() body starts at line 6, mul3() at line 11. Do not reflow. */
#include "mathlib.h"

int add(int a, int b)
{
    return a + b;
}

int mul3(int a)
{
    return a * 3;
}
