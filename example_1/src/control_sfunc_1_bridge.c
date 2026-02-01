#ifdef MATLAB_MEX_FILE
  #include "tmwtypes.h"
#else
  #include "rtwtypes.h"
#endif

/* Implemented in src/control_1.c (your original working wrappers) */
extern void control_Start_wrapper(void);
extern void control_Outputs_wrapper(const real_T *u0, real_T *y0);
extern void control_Terminate_wrapper(void);

/* Expected by model/control_sfunc_1.c (because S-function name is control_sfunc_1) */
void control_sfunc_1_Start_wrapper(void) { control_Start_wrapper(); }

void control_sfunc_1_Outputs_wrapper(const real_T *u0, real_T *y0)
{
    control_Outputs_wrapper(u0, y0);
}

void control_sfunc_1_Terminate_wrapper(void) { control_Terminate_wrapper(); }
