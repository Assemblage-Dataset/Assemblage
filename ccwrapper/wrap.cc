// a  wrapper for c compiler use can unset some compiler flag and bypass all other flags

// Yihao Sun
// 2021 Syracuse

#include <unistd.h>
#include <iostream>
#include <cstring>
#include <vector>

bool start_with(const char *pre, const char *str)
{
    size_t lenpre = strlen(pre),
           lenstr = strlen(str);
    return lenstr < lenpre ? false : memcmp(pre, str, lenpre) == 0;
}

int main(int argc, char *argv[])
{

    std::vector<char *> new_argv_vec;
#ifdef CC
    new_argv_vec.push_back(CC);
#else
    new_argv_vec.push_back("clang++");
#endif

#ifdef TARGET_FLAG
    new_argv_vec.push_back(TARGET_FLAG);
#endif
    // new_argv_vec.push_back(TARGET_VALUE);
    for (int i = 1; i < argc; i++)
    {
// #ifdef TARGET_FLAG
//         if (strcmp(argv[i], TARGET_FLAG) == 0)
//         {
//             i++;
//             while (argv[i][0] != '-' && i < argc)
//             {
//                 i++;
//                 break;
//             }
//             i--;
//             continue;
//         }
//         if (start_with(TARGET_FLAG, argv[i]))
//         {
//             continue;
//         }
// #endif
#ifdef UNSET_FLAG
        if (start_with(UNSET_FLAG, argv[i]))
        {
            continue;
        }
#endif
        new_argv_vec.push_back(argv[i]);
    }
    for (char *s : new_argv_vec)
    {
        printf("%s ", s);
    }
#ifdef CC
    execv("/usr/bin/CC", &new_argv_vec[0]);
#else
    execv("/usr/bin/clang++", &new_argv_vec[0]);
#endif
    return 0;
}
