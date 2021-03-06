import numpy
import numpy.linalg
import scipy.linalg
import scipy.interpolate

from matplotlib import pyplot as plt
plt.style.use('classic')

import scipy.sparse as sps

from assimulo.solvers import IDA
from assimulo.problem import Implicit_Problem

from scipy.sparse.linalg import spsolve as sparseSolve
from scipy.sparse import csr_matrix as sparseMat
import scipy.sparse as sparse
import math
from copy import deepcopy


def compute_deriv( func, x0 ) :

    y0 = func(x0)

    J = numpy.zeros( (len(x0),len(x0)), dtype='d' )

    x_higher = deepcopy(x0)
    
    eps = 1e-8

    for ivar in range(len(x0)) :

        x_higher[ivar] = x_higher[ivar] + eps

        # evaluate the function
        y_higher = func(x_higher)

        dy_dx = (y_higher-y0) / eps

        J[:,ivar] = dy_dx

        x_higher[ivar] = x0[ivar]

    return J


def mid_to_edge( var_mid, x_e ) :

    var_edge = numpy.array( [var_mid[0]] + [ var_mid[i]*var_mid[i+1]/( ((x_e[i+1]-x_e[i])/((x_e[i+2]-x_e[i+1])+(x_e[i+1]-x_e[i])))*var_mid[i+1] + (1- ((x_e[i+1]-x_e[i])/((x_e[i+2]-x_e[i+1])+(x_e[i+1]-x_e[i]))))*var_mid[i] ) for i in range(len(var_mid)-1) ] + [var_mid[-1]] )

    return var_edge

def flux_mat_builder( N, x_m, vols, P ) :

    A = numpy.zeros([N,N], dtype='d')

    for i in range(1,N-1) :

        A[i,i-1] =  (1./vols[i]) * (P[i  ]) / (x_m[i  ] - x_m[i-1])
        A[i,i  ] = -(1./vols[i]) * (P[i  ]) / (x_m[i  ] - x_m[i-1]) - (1./vols[i]) * (P[i+1]) / (x_m[i+1] - x_m[i])
        A[i,i+1] =  (1./vols[i]) * (P[i+1]) / (x_m[i+1] - x_m[i  ])

    i=0
    A[0,0] = -(1./vols[i]) * (P[i+1]) / (x_m[i+1] - x_m[i])
    A[0,1] =  (1./vols[i]) * (P[i+1]) / (x_m[i+1] - x_m[i])

    i=N-1
    A[i,i-1] =  (1./vols[i]) * (P[i]) / (x_m[i] - x_m[i-1])
    A[i,i  ] = -(1./vols[i]) * (P[i]) / (x_m[i] - x_m[i-1])

    return A

def grad_mat( N, x ) :

    G = numpy.zeros( [N,N] )
    for i in range(1,N-1) :
        G[i,[i-1, i+1]] = [ -1./(x[i+1]-x[i-1]), 1./(x[i+1]-x[i-1]) ]
    G[0,[0,1]] = [-1./(x[1]-x[0]),1./(x[1]-x[0])]
    G[-1,[-2,-1]] = [-1./(x[-1]-x[-2]),1./(x[-1]-x[-2])]

    return G


def build_interp_2d( path ) :

    raw_map = numpy.loadtxt( path, delimiter="," )

    v1 = raw_map[1:,0]
    v2 = raw_map[0,1:]

    dat_map = raw_map[1:,1:]

    if v1[1] < v1[0] :
        v1 = numpy.flipud( v1 )
        dat_map = numpy.flipud(dat_map)

    if v2[1] < v2[0] :
        v2 = numpy.flipud( v2 )
        dat_map = numpy.fliplr(dat_map)

    return scipy.interpolate.RectBivariateSpline( v1, v2, dat_map )    

class MyProblem( Implicit_Problem ) :

    def __init__(self, Na, Ns, Nc, X, Ac, bsp_dir, y0, yd0, name ) :

        Implicit_Problem.__init__(self,y0=y0,yd0=yd0,name=name)

        self.Ac = Ac # Cell coated area, [m^2]

        # Control volumes and node points (mid node points and edge node points)
        self.Ns = Ns
        self.Na = Na
        self.Nc = Nc

        self.N = Na + Ns + Nc
        self.X = X

        self.num_diff_vars = N + Na + Nc + 1
        self.num_algr_vars = Na + Nc + N + Na + Nc

        self.x_e  = numpy.linspace( 0.0, X, N+1 )
        self.x_m  = numpy.array( [ 0.5*(self.x_e[i+1]+self.x_e[i]) for i in range(N) ], dtype='d'  )
        self.vols = numpy.array( [ (self.x_e[i+1] - self.x_e[i]) for i in range(N)], dtype='d' )

        # Useful sub-meshes for the phi_s functions
        self.x_m_a = self.x_m[:Na]
        self.x_m_c = self.x_m[-Nc:]
        self.x_e_a = self.x_e[:Na+1]
        self.x_e_c = self.x_e[-Nc-1:]

        self.vols_a = self.vols[:Na]
        self.vols_c = self.vols[-Nc:]

        self.La, self.Ls, self.Lc = self.Na*X/self.N, self.Ns*X/self.N, self.Nc*X/self.N
        self.Na, self.Ns, self.Nc = Na, Ns, Nc

        # System indices
        self.ce_inds  = range(self.N)
        self.csa_inds = range(self.N, self.N+self.Na)
        self.csc_inds = range(self.N+self.Na, self.N+self.Na+self.Nc)

        self.T_ind = self.N+self.Na+self.Nc
        c_end = self.N+self.Na+self.Nc + 1

        self.ja_inds = range(c_end, c_end+self.Na)
        self.jc_inds = range(c_end+self.Na, c_end+self.Na +self.Nc)

        self.pe_inds   = range( c_end+self.Na+self.Nc, c_end+self.Na+self.Nc +self.N )
        self.pe_a_inds = range( c_end+self.Na+self.Nc, c_end+self.Na+self.Nc +self.Na )
        self.pe_c_inds = range( c_end+self.Na+self.Nc +self.Na+self.Ns, c_end+self.Na+self.Nc +self.N )

        self.pa_inds = range( c_end+self.Na+self.Nc+self.N, c_end+self.Na+self.Nc+self.N +self.Na )
        self.pc_inds = range( c_end+self.Na+self.Nc+self.N+self.Na, c_end+self.Na+self.Nc+self.N+self.Na +self.Nc )

        # second set for manual jac version
        c_end = 0
        self.ja_inds2 = range(c_end, c_end+self.Na)
        self.ja_inds_r2 = numpy.reshape( self.ja_inds2, [len(self.ja_inds2),1] )
        self.ja_inds_c2 = numpy.reshape( self.ja_inds2, [1,len(self.ja_inds2)] )

        self.jc_inds2 = range(c_end+self.Na, c_end+self.Na +self.Nc)
        self.jc_inds_r2 = numpy.reshape( self.jc_inds2, [len(self.jc_inds2),1] )
        self.jc_inds_c2 = numpy.reshape( self.jc_inds2, [1,len(self.jc_inds2)] )
        
        self.pe_inds2   = range( c_end+self.Na+self.Nc, c_end+self.Na+self.Nc +self.N )
        self.pe_inds_r2 = numpy.reshape( self.pe_inds2, [len(self.pe_inds2),1] )
        self.pe_inds_c2 = numpy.reshape( self.pe_inds2, [1,len(self.pe_inds2)] )

        self.pe_a_inds2 = range( c_end+self.Na+self.Nc, c_end+self.Na+self.Nc +self.Na )
        self.pe_a_inds_r2 = numpy.reshape( self.pe_a_inds2, [len(self.pe_a_inds2),1] )
        self.pe_a_inds_c2 = numpy.reshape( self.pe_a_inds2, [1,len(self.pe_a_inds2)] )

        self.pe_c_inds2 = range( c_end+self.Na+self.Nc +self.Na+self.Ns, c_end+self.Na+self.Nc +self.N )
        self.pe_c_inds_r2 = numpy.reshape( self.pe_c_inds2, [len(self.pe_c_inds2),1] )
        self.pe_c_inds_c2 = numpy.reshape( self.pe_c_inds2, [1,len(self.pe_c_inds2)] )

        self.pa_inds2 = range( c_end+self.Na+self.Nc+self.N, c_end+self.Na+self.Nc+self.N +self.Na )
        self.pa_inds_r2 = numpy.reshape( self.pa_inds2, [len(self.pa_inds2),1] )
        self.pa_inds_c2 = numpy.reshape( self.pa_inds2, [1,len(self.pa_inds2)] )

        self.pc_inds2 = range( c_end+self.Na+self.Nc+self.N+self.Na, c_end+self.Na+self.Nc+self.N+self.Na +self.Nc )
        self.pc_inds_r2 = numpy.reshape( self.pc_inds2, [len(self.pc_inds2),1] )
        self.pc_inds_c2 = numpy.reshape( self.pc_inds2, [1,len(self.pc_inds2)] )

        # Volume fraction vectors and matrices for effective parameters
        eps_a = 0.25
        eps_s = 0.5
        eps_c = 0.2
        ba, bs, bc = 1.2, 0.5, 0.5

        eps_a_vec = [ eps_a for i in range(Na) ] # list( eps_a + eps_a/2.*numpy.sin(numpy.linspace(0.,Na/4,Na)) ) # list(eps_a + eps_a*numpy.random.randn(Na)/5.) #
        eps_s_vec = [ eps_s for i in range(Ns) ]
        eps_c_vec = [ eps_c for i in range(Nc) ] # list( eps_c + eps_c/2.*numpy.sin(numpy.linspace(0.,Nc/4,Nc)) ) # list(eps_c + eps_c*numpy.random.randn(Nc)/5.) #

        self.eps_m   = numpy.array( eps_a_vec + eps_s_vec + eps_c_vec, dtype='d' )
        self.k_m     = 1./self.eps_m
        self.eps_mb  = numpy.array( [ ea**ba for ea in eps_a_vec ] + [ es**bs for es in eps_s_vec ] + [ ec**bc for ec in eps_c_vec ], dtype='d' )
        self.eps_eff = numpy.array( [ ea**(1.+ba) for ea in eps_a_vec ] + [ es**(1.+bs) for es in eps_s_vec ] + [ ec**(1.+bc) for ec in eps_c_vec ], dtype='d' )

        self.eps_a_eff = self.eps_eff[:Na]
        self.eps_c_eff = self.eps_eff[-Nc:]

        self.K_m = numpy.diag( self.k_m )

        t_plus = 0.43
        F = 96485.0

        self.t_plus = t_plus
        self.F = F
        self.R_gas = 8.314

        Rp_a = 12.0e-6
        Rp_c = 6.5e-6
        self.Rp_a = Rp_a
        self.Rp_c = Rp_c

        as_a = 3.*(1.0-numpy.array(eps_a_vec, dtype='d'))/Rp_a
        as_c = 3.*(1.0-numpy.array(eps_c_vec, dtype='d'))/Rp_c
        self.as_a = as_a
        self.as_c = as_c

        self.as_a_mean = 1./self.La*sum( [ asa*v for asa,v in zip(as_a, self.vols[:Na]) ] )
        self.as_c_mean = 1./self.Lc*sum( [ asc*v for asc,v in zip(as_c, self.vols[-Nc:]) ] )

        print 'asa diff', self.as_a_mean - as_a[0]
        print 'asc diff', self.as_c_mean - as_c[0]

        Ba = [ (1.-t_plus)*asa/ea for ea, asa in zip(eps_a_vec,as_a) ]
        Bs = [  0.0                for i in range(Ns) ]
        Bc = [ (1.-t_plus)*asc/ec for ec, asc in zip(eps_c_vec,as_c) ]

        self.B_ce = numpy.diag( numpy.array(Ba+Bs+Bc, dtype='d') )

        Bap = [ asa*F for asa in as_a  ]
        Bsp = [   0.0 for i   in range(Ns) ]
        Bcp = [ asc*F for asc in as_c  ]

        self.B2_pe = numpy.diag( numpy.array(Bap+Bsp+Bcp, dtype='d') )

        # Interpolators for De, ke
        self.De_intp  = build_interp_2d( bsp_dir+'data/Model_v1/Model_Pars/electrolyte/De.csv' )
        self.ke_intp  = build_interp_2d( bsp_dir+'data/Model_v1/Model_Pars/electrolyte/kappa.csv' )
        self.fca_intp = build_interp_2d( bsp_dir+'data/Model_v1/Model_Pars/electrolyte/fca.csv' )

        self.ce_nom = 1000.0

        ######
        ## Solid phase parameters and j vector matrices
        self.sig_a = 100. # [S/m]
        self.sig_c = 40. # [S/m]

        self.sig_a_eff = self.sig_a * (1.0-self.eps_a_eff)
        self.sig_c_eff = self.sig_c * (1.0-self.eps_c_eff)

        self.A_ps_a = flux_mat_builder( self.Na, self.x_m_a, numpy.ones_like(self.vols_a), self.sig_a_eff )
        self.A_ps_c = flux_mat_builder( self.Nc, self.x_m_c, numpy.ones_like(self.vols_c), self.sig_c_eff )

        # Grounding form for BCs (was only needed during testing, before BVK was incorporated for coupling
        Baps = numpy.array( [ asa*F*dxa for asa,dxa in zip(as_a, self.vols_a) ], dtype='d' )
        Bcps = numpy.array( [ asc*F*dxc for asc,dxc in zip(as_c, self.vols_c) ], dtype='d' )
        self.B_ps_a = numpy.diag( Baps )
        self.B_ps_c = numpy.diag( Bcps )

        self.B2_ps_a = numpy.zeros( self.Na, dtype='d' )
        self.B2_ps_a[ 0] = -1.
        self.B2_ps_c = numpy.zeros( self.Nc, dtype='d' )
        self.B2_ps_c[-1] = -1.

        # Thermal
        self.T  = y0[self.T_ind] # Cell temperature, [K]
        self.T_amb = self.T # ambient convection temperature

        # Two parameter Solid phase diffusion model
        Ea_Dsa, Ea_Dsc = 0.0, 0.0
        Dsa = 1e-12*math.exp( Ea_Dsa/self.R_gas*(1/298.25-1/self.T) )
        Dsc = 1e-14*math.exp( Ea_Dsc/self.R_gas*(1/298.25-1/self.T) )
        self.Dsa = Dsa
        self.Dsc = Dsc

        self.csa_max = 30555.0 # [mol/m^3]
        self.csc_max = 51554.0 # [mol/m^3]

        self.B_cs_a = numpy.diag( numpy.array( [-3.0/Rp_a for i in range(Na)], dtype='d' ) ) 
        self.B_cs_c = numpy.diag( numpy.array( [-3.0/Rp_c for i in range(Nc)], dtype='d' ) ) 

        self.C_cs_a = numpy.eye(Na)
        self.C_cs_c = numpy.eye(Nc)

        
        self.D_cs_a = numpy.diag( numpy.array( [-Rp_a/Dsa/5.0 for i in range(Na)], dtype='d' ) ) 
        self.D_cs_c = numpy.diag( numpy.array( [-Rp_c/Dsc/5.0 for i in range(Nc)], dtype='d' ) ) 

#        bsp_dir = '/home/m_klein/Projects/battsimpy/'
#        bsp_dir = '/home/mk-sim-linux/Battery_TempGrad/Python/batt_simulation/battsimpy/'

        uref_a_map = numpy.loadtxt( bsp_dir + '/data/Model_v1/Model_Pars/solid/thermodynamics/uref_anode_x.csv'  , delimiter=',' )
        uref_c_map = numpy.loadtxt( bsp_dir + '/data/Model_v1/Model_Pars/solid/thermodynamics/uref_cathode_x.csv', delimiter=',' )

        duref_a = numpy.gradient( uref_a_map[:,1] ) / numpy.gradient( uref_a_map[:,0] )
        duref_c = numpy.gradient( uref_c_map[:,1] ) / numpy.gradient( uref_c_map[:,0] )

        if uref_a_map[1,0] > uref_a_map[0,0] :
            self.uref_a_interp  = scipy.interpolate.interp1d( uref_a_map[:,0],  uref_a_map[:,1] )
            self.duref_a_interp = scipy.interpolate.interp1d( uref_a_map[:,0], duref_a      )
        else :
            self.uref_a_interp  = scipy.interpolate.interp1d( numpy.flipud(uref_a_map[:,0]), numpy.flipud(uref_a_map[:,1]) )
            self.duref_a_interp = scipy.interpolate.interp1d( numpy.flipud(uref_a_map[:,0]), numpy.flipud(duref_a)     )

        if uref_c_map[1,0] > uref_c_map[0,0] :
            self.uref_c_interp  = scipy.interpolate.interp1d( uref_c_map[:,0], uref_c_map[:,1] )
            self.duref_c_interp = scipy.interpolate.interp1d( uref_c_map[:,0], duref_c     )
        else :
            self.uref_c_interp  = scipy.interpolate.interp1d( numpy.flipud(uref_c_map[:,0]), numpy.flipud(uref_c_map[:,1]) )
            self.duref_c_interp = scipy.interpolate.interp1d( numpy.flipud(uref_c_map[:,0]), numpy.flipud(duref_c)     )

        # Reaction kinetics parameters
        # Interpolators for io_a, io_c
        self.ioa_interp = build_interp_2d( bsp_dir+'data/Model_v1/Model_Pars/solid/kinetics/io_anode.csv' )
        self.ioc_interp = build_interp_2d( bsp_dir+'data/Model_v1/Model_Pars/solid/kinetics/io_cathode.csv' )

        self.io_a = 5.0
        self.io_c = 5.0

        ### Matrices for thermal calcs (gradient operators)
        self.h, self.Aconv, self.rho, self.Cp = 100.0, 30., 2250.*self.X, 1200. # conv heat coeff [W/m^2-K], conv area ratio (Aconv/Acoat) [m^2/m^2], density per coated area [kg/m^2], specific heat capacity [J/kg-K]
        self.Ga, self.Gc, self.G = grad_mat( Na, self.x_m_a ), grad_mat( Nc, self.x_m_c ), grad_mat( N, self.x_m )

        # Initialize the C arrays
        junkQ = self.calc_heat( y0, numpy.zeros(Na), numpy.zeros(Nc), self.uref_a_interp( y0[self.csa_inds]/self.csa_max ), self.uref_c_interp( y0[self.csc_inds]/self.csc_max ) )

        csa_ss = y0[self.csa_inds]
        csc_ss = y0[self.csc_inds]
        ce = y0[self.ce_inds]
        T  = y0[self.T_ind]
        self.C_ioa = 2.0*self.ioa_interp(csa_ss/self.csa_max, T, grid=False).flatten()/self.F * numpy.sqrt( ce[:self.Na ]/self.ce_nom * (1.0 - csa_ss/self.csa_max) * (csa_ss/self.csa_max) )
        self.C_ioc = 2.0*self.ioc_interp(csc_ss/self.csc_max, T, grid=False).flatten()/self.F * numpy.sqrt( ce[-self.Nc:]/self.ce_nom * (1.0 - csc_ss/self.csc_max) * (csc_ss/self.csc_max) )

#        self.C_ioa = (2.0*self.io_a/self.F) * numpy.ones_like( csa_ss )
#        self.C_ioc = (2.0*self.io_a/self.F) * numpy.ones_like( csc_ss )


    def set_iapp( self, I_app ) :
        self.i_app = I_app / self.Ac

    ## Define c_e functions
    def build_Ace_mat( self, c, T ) :

        D_eff = self.Diff_ce( c, T )

        A = self.K_m.dot( flux_mat_builder( self.N, self.x_m, self.vols, D_eff ) )

        return A

    def Diff_ce( self, c, T ) :

#        T = self.T

#        D_ce = 1e-4 * 10.0**( -4.43 - (54./(T-229.-5e-3*c)) - (0.22e-3*c) )  ## Torchio (LIONSIMBA) ECS paper

        D_ce = self.De_intp( c, T, grid=False ).flatten()
        
        D_mid = D_ce * self.eps_eff

        if type(c) == float :
            D_edge = D_mid
        else :
            D_edge = mid_to_edge( D_mid, self.x_e )

        return D_edge

    ## Define phi_e functions
    def build_Ape_mat( self, c, T ) :

        k_eff = self.kapp_ce( c, T )

        A = flux_mat_builder( self.N, self.x_m, self.vols, k_eff )

        A[-1,-1] = 2*A[-1,-1]

        return A

    def build_Bpe_mat( self, c, T ) :

        gam = 2.*(1.-self.t_plus)*self.R_gas*T / self.F

        k_eff = self.kapp_ce( c, T )

        c_edge = mid_to_edge( c, self.x_e )

        B1 = flux_mat_builder( self.N, self.x_m, self.vols, k_eff*gam/c_edge )

        return B1

    def kapp_ce( self, c, T, mid_on=0 ) :

#        T = self.T

#        k_ce = 1e-4 * c *(   -10.5 +0.668e-3*c + 0.494e-6*c**2
#                            + (0.074 - 1.78*1e-5*c - 8.86e-10*c**2)*T 
#                            + (-6.96e-5 + 2.8e-8*c)*T**2 )**2  ## Torchio (LIONSIMBA) ECS paper

        k_ce = 1e-1*self.ke_intp( c, T, grid=False ).flatten() # 1e-1 converts from mS/cm to S/m (model uses SI units)
                           

        k_mid = k_ce * self.eps_eff

        if mid_on :
            k_out = k_mid
        else :
            if type(c) == float :
                k_out = k_mid
            else :
                k_out = mid_to_edge( k_mid, self.x_e )

        return k_out

    def build_Bjac_mat( self, eta, a, b ) :
            
        d = a*numpy.cosh( b*eta )*b

        return numpy.diag( d )

    def build_BjT_mat( self, T, a, b ) :
            
        d = a*numpy.cosh( b/T )*(-b/T**2)

        return d


    def get_voltage( self, y ) :
        """
        Return the cell potential
        """
        pc = y[self.pc_inds]
        pa = y[self.pa_inds]

        Vcell = pc[-1] - pa[0]

        return Vcell


    def calc_heat( self, y, eta_a, eta_c, Uref_a, Uref_c ) :
        """
        Return the total integrated heat source across the cell sandwich
        """
        ce      = y[ self.ce_inds  ]
        csa     = y[ self.csa_inds ]
        csc     = y[ self.csc_inds ]
        ja      = y[ self.ja_inds  ]
        jc      = y[ self.jc_inds  ]
        phi     = y[ self.pe_inds  ]
        phi_s_a = y[ self.pa_inds  ]
        phi_s_c = y[ self.pc_inds  ]
        T = y[self.T_ind]

        # Gradients for heat calc
        dphi_s_a = numpy.gradient( phi_s_a ) / numpy.gradient( self.x_m_a )
        dphi_s_c = numpy.gradient( phi_s_c ) / numpy.gradient( self.x_m_c )

        dphi = numpy.gradient( phi ) / numpy.gradient( self.x_m )

        dlnce = 1./ce * ( numpy.gradient(ce) / numpy.gradient( self.x_m ) )

        kapp_eff_m = self.kapp_ce( ce, T, mid_on=1 ) # kapp_eff at the node points (middle of control volume, rather than edge)

#        dp = self.G.dot(phi)

        # Reaction kinetics heat
        C_ra = (self.vols_a*self.F*self.as_a)
        C_rc = (self.vols_c*self.F*self.as_c)

        Q_rxn_a = C_ra.dot( ja*eta_a )
        Q_rxn_c = C_rc.dot( jc*eta_c )
        Q_rxn = Q_rxn_a + Q_rxn_c

        csa_mean = numpy.mean(csa)
        csc_mean = numpy.mean(csc)
        Uam = self.uref_a_interp( csa_mean/self.csa_max )
        Ucm = self.uref_c_interp( csc_mean/self.csc_max )

        eta_conc_a = Uref_a-Uam
        eta_conc_c = Uref_c-Ucm

        Q_conc_a = C_ra.dot( eta_conc_a*ja )
        Q_conc_c = C_rc.dot( eta_conc_c*jc )

        Q_conc = Q_conc_a + Q_conc_c

        # Ohmic heat in electrolyte and solid
        C_pe = (self.vols.dot( numpy.diag(kapp_eff_m*dphi).dot(self.G) ) + 
                self.vols.dot( numpy.diag(2*kapp_eff_m*self.R_gas*T/self.F*(1.-self.t_plus)*dlnce).dot(self.G) ))

        Q_ohm_e = C_pe.dot(phi)

        C_pa = self.vols_a.dot( numpy.diag(self.sig_a_eff*dphi_s_a).dot(self.Ga) )
        C_pc = self.vols_c.dot( numpy.diag(self.sig_c_eff*dphi_s_c).dot(self.Gc) )

        Q_ohm_s = C_pa.dot(phi_s_a) + C_pc.dot(phi_s_c)

        Q_ohm = Q_ohm_e + Q_ohm_s

        # Entropic heat
        ## ??

        # Total heat
        Q_tot = Q_ohm + Q_rxn + Q_conc

        self.C_q_pe = C_pe
        self.C_q_pa = C_pa
        self.C_q_pc = C_pc
        self.C_q_na = C_ra*ja
        self.C_q_nc = C_rc*jc
        self.C_q_ja = C_ra*eta_a + C_ra*eta_conc_a
        self.C_q_jc = C_rc*eta_c + C_rc*eta_conc_c

        return Q_tot

    def get_eta_uref( self, csa, csc, ja_rxn, jc_rxn, phi_s_a, phi_s_c, phi ) :

        csa_ss = csa + (self.D_cs_a.dot( ja_rxn ).flatten()) # anode   surface conc
        csc_ss = csc + (self.D_cs_c.dot( jc_rxn ).flatten()) # cathode surface conc

        Uref_a = self.uref_a_interp( csa_ss/self.csa_max ) # anode   equilibrium potential
        Uref_c = self.uref_c_interp( csc_ss/self.csc_max ) # cathode equilibrium potential

        eta_a  = phi_s_a - phi[:self.Na]  - Uref_a  # anode   overpotential
        eta_c  = phi_s_c - phi[-self.Nc:] - Uref_c  # cathode overpotential

        return eta_a, eta_c, Uref_a, Uref_c, csa_ss, csc_ss

    def update_Cio( self, csa_ss, csc_ss, ce, T ) :

        self.C_ioa = 2.0*self.ioa_interp(csa_ss/self.csa_max, T, grid=False).flatten()/self.F * numpy.sqrt( ce[:self.Na ]/self.ce_nom * (1.0 - csa_ss/self.csa_max) * (csa_ss/self.csa_max) )
        self.C_ioc = 2.0*self.ioc_interp(csc_ss/self.csc_max, T, grid=False).flatten()/self.F * numpy.sqrt( ce[-self.Nc:]/self.ce_nom * (1.0 - csc_ss/self.csc_max) * (csc_ss/self.csc_max) )


    ## Define system equations
    def res( self, t, y, yd ) :

        ## Parse out the states
        # E-lyte conc
        ce     = y[ self.ce_inds]
        c_dots = yd[self.ce_inds]

        # Solid conc a:anode, c:cathode
        csa    = y[ self.csa_inds]
        csc    = y[ self.csc_inds]
        csa_dt = yd[self.csa_inds]
        csc_dt = yd[self.csc_inds]

        # Reaction (Butler-Volmer Kinetics)
        ja_rxn = y[self.ja_inds]
        jc_rxn = y[self.jc_inds]

        # E-lyte potential
        phi = y[self.pe_inds]

        # Solid potential
        phi_s_a = y[self.pa_inds]
        phi_s_c = y[self.pc_inds]

        # Temp
        T = y[self.T_ind]
        T_dt = yd[self.T_ind]

        ## Grab state dependent matrices
        # For E-lyte conc and potential (i.e., De(ce), kapp_e(ce))
        A_ce = self.build_Ace_mat( ce, T )
        A_pe = self.build_Ape_mat( ce, T )
        B_pe = self.build_Bpe_mat( ce, T )

        ## Compute extra variables
        # For the reaction kinetics
        eta_a, eta_c, Uref_a, Uref_c, csa_ss, csc_ss = self.get_eta_uref( csa, csc, ja_rxn, jc_rxn, phi_s_a, phi_s_c, phi )

        # For kinetics, the io param is now conc dependent
        self.update_Cio( csa_ss, csc_ss, ce, T )

        Q_in = self.calc_heat( y, eta_a, eta_c, Uref_a, Uref_c )

        Q_out = (self.h*self.Aconv)*(T - self.T_amb)

#        ja = (2.0*self.io_a/self.F) * numpy.sinh( 0.5*self.F/(self.R_gas*self.T)*eta_a )
#        jc = (2.0*self.io_c/self.F) * numpy.sinh( 0.5*self.F/(self.R_gas*self.T)*eta_c )
        ja = self.C_ioa * numpy.sinh( 0.5*self.F/(self.R_gas*T)*eta_a )
        jc = self.C_ioc * numpy.sinh( 0.5*self.F/(self.R_gas*T)*eta_c )

        j = numpy.concatenate( [ ja_rxn, numpy.zeros(self.Ns), jc_rxn ] )

        ## Compute the residuals
        # Time deriv components
        r1 = c_dots - ( ((A_ce.dot(ce)).flatten() + (self.B_ce.dot(j)).flatten()) ) # E-lyte conc

        r2 = csa_dt - (self.B_cs_a.dot(ja_rxn).flatten()) # Anode   conc
        r3 = csc_dt - (self.B_cs_c.dot(jc_rxn).flatten()) # Cathode conc        

        r4 = T_dt - 1./(self.rho*self.Cp)*( Q_in - Q_out )

        # Algebraic components
        r5 = ja_rxn - ja
        r6 = jc_rxn - jc 

        r7 = A_pe.dot(phi).flatten() - B_pe.dot(ce).flatten() + self.B2_pe.dot(j).flatten() # E-lyte potential

        r8 = self.A_ps_a.dot(phi_s_a).flatten() - self.B_ps_a.dot(ja_rxn).flatten() - self.B2_ps_a*self.i_app # Anode   potential #+ extra #
        r9 = self.A_ps_c.dot(phi_s_c).flatten() - self.B_ps_c.dot(jc_rxn).flatten() + self.B2_ps_c*self.i_app # Cathode potential

        res_out = numpy.concatenate( [r1, r2, r3, [r4], r5, r6, r7, r8, r9] )

        return res_out

    def jac( self, c, t, y, yd ) :

        ### Setup 
        ## Parse out the states
        # E-lyte conc
        ce     = y[ self.ce_inds]
#        c_dots = yd[self.ce_inds]

        # Solid conc a:anode, c:cathode
        csa    = y[ self.csa_inds]
        csc    = y[ self.csc_inds]
#        csa_dt = yd[self.csa_inds]
#        csc_dt = yd[self.csc_inds]

        T = y[self.T_ind]

        # Reaction (Butler-Volmer Kinetics)
        ja_rxn = y[self.ja_inds]
        jc_rxn = y[self.jc_inds]

        # E-lyte potential
        phi = y[self.pe_inds]

        # Solid potential
        phi_s_a = y[self.pa_inds]
        phi_s_c = y[self.pc_inds]

        ## Grab state dependent matrices
        # For E-lyte conc and potential (i.e., De(ce), kapp_e(ce))
        A_ce = self.build_Ace_mat( ce, T )
        A_pe = self.build_Ape_mat( ce, T )
        B_pe = self.build_Bpe_mat( ce, T )

        ## Compute extra variables
        # For the reaction kinetics
        eta_a, eta_c, Uref_a, Uref_c, csa_ss, csc_ss = self.get_eta_uref( csa, csc, ja_rxn, jc_rxn, phi_s_a, phi_s_c, phi )
        ###

        dcss_dcs_a = 1.0
        dcss_dcs_c = 1.0

        dcss_dja = numpy.diagonal( self.D_cs_a )
        dcss_djc = numpy.diagonal( self.D_cs_c )

        ### Build the Jac matrix
        ## Self coupling
        A_dots = numpy.diag( [1*c for i in range(self.num_diff_vars)] )
        j_c    = A_dots - scipy.linalg.block_diag( A_ce, numpy.zeros([self.Na,self.Na]), numpy.zeros([self.Nc,self.Nc]), [-self.h*self.Aconv/self.rho/self.Cp] )

        Bjac_a = self.build_Bjac_mat( eta_a, self.C_ioa, 0.5*self.F/(self.R_gas*T) )
        Bjac_c = self.build_Bjac_mat( eta_c, self.C_ioc, 0.5*self.F/(self.R_gas*T) )

        BjT_a = self.build_BjT_mat( T, self.C_ioa, 0.5*self.F/(self.R_gas)*eta_a )
        BjT_c = self.build_BjT_mat( T, self.C_ioc, 0.5*self.F/(self.R_gas)*eta_c )

        dU_csa_ss = (1.0/self.csa_max)*self.duref_a_interp(csa_ss/self.csa_max)
        dU_csc_ss = (1.0/self.csc_max)*self.duref_c_interp(csc_ss/self.csc_max)

        DUDcsa_ss = numpy.diag( dU_csa_ss )
        DUDcsc_ss = numpy.diag( dU_csc_ss )

        A_ja = numpy.diag(numpy.ones(self.Na)) - Bjac_a.dot(DUDcsa_ss.dot(-1.0*self.D_cs_a))
        A_jc = numpy.diag(numpy.ones(self.Nc)) - Bjac_c.dot(DUDcsc_ss.dot(-1.0*self.D_cs_c))

        j = scipy.linalg.block_diag( j_c, A_ja, A_jc, A_pe, self.A_ps_a, self.A_ps_c )

        ## Cross coupling
        # c_e: j coupling back in
        j[ numpy.ix_(self.ce_inds, self.ja_inds) ] = -self.B_ce[:, :self.Na]
        j[ numpy.ix_(self.ce_inds, self.jc_inds) ] = -self.B_ce[:, -self.Nc:]

        # cs_a: j coupling
        j[ numpy.ix_(self.csa_inds, self.ja_inds) ] = -self.B_cs_a
        # cs_c: j coupling
        j[ numpy.ix_(self.csc_inds, self.jc_inds) ] = -self.B_cs_c

        # T
        j[self.T_ind,self.ja_inds]  = -1./(self.rho*self.Cp)*(self.C_q_ja + 2.0*(self.C_q_na*(-1.0)*dU_csa_ss*dcss_dja))
        j[self.T_ind,self.jc_inds]  = -1./(self.rho*self.Cp)*(self.C_q_jc + 2.0*(self.C_q_nc*(-1.0)*dU_csc_ss*dcss_djc))
        j[self.T_ind,self.pe_inds]  = -1./(self.rho*self.Cp)*(self.C_q_pe + numpy.array( list(self.C_q_na)+[0. for i in range(self.Ns)]+list(self.C_q_nc) )*(-1.0))
        j[self.T_ind,self.pa_inds]  = -1./(self.rho*self.Cp)*(self.C_q_pa + self.C_q_na*(1.0))
        j[self.T_ind,self.pc_inds]  = -1./(self.rho*self.Cp)*(self.C_q_pc + self.C_q_nc*(1.0))
        j[self.T_ind,self.csa_inds] = -1./(self.rho*self.Cp)*(2.0*(self.C_q_na*(-1.0)*dU_csa_ss*dcss_dcs_a))
        j[self.T_ind,self.csc_inds] = -1./(self.rho*self.Cp)*(2.0*(self.C_q_nc*(-1.0)*dU_csc_ss*dcss_dcs_c))

        j[self.ja_inds,self.T_ind] = -BjT_a
        j[self.jc_inds,self.T_ind] = -BjT_c

        # j_a: pe, pa, csa  coupling
        j[numpy.ix_(self.ja_inds, self.pa_inds  )] = -Bjac_a*( 1.0)
        j[numpy.ix_(self.ja_inds, self.pe_a_inds)] = -Bjac_a*(-1.0)
        j[numpy.ix_(self.ja_inds, self.csa_inds )] = -Bjac_a.dot(-1.0*DUDcsa_ss*1.0)
        # j_c: pe, pc, csc  coupling         
        j[numpy.ix_(self.jc_inds, self.pc_inds  )] = -Bjac_c*( 1.0)
        j[numpy.ix_(self.jc_inds, self.pe_c_inds)] = -Bjac_c*(-1.0)
        j[numpy.ix_(self.jc_inds, self.csc_inds )] = -Bjac_c.dot(-1.0*DUDcsc_ss*1.0)

        # phi_e: ce coupling into phi_e equation
        j[numpy.ix_(self.pe_inds,self.ce_inds)] = -B_pe
        j[numpy.ix_(self.pe_inds,self.ja_inds)] = self.B2_pe[:,:self.Na]
        j[numpy.ix_(self.pe_inds,self.jc_inds)] = self.B2_pe[:,-self.Nc:]

        # phi_s_a: ja
        j[numpy.ix_(self.pa_inds,self.ja_inds)] = -self.B_ps_a
        # phi_s_c: jc
        j[numpy.ix_(self.pc_inds,self.jc_inds)] = -self.B_ps_c
        ###        

        return j




csa_max = 30555.0 # [mol/m^3]
csc_max = 51554.0 # [mol/m^3]

bsp_dir = '/home/m_klein/Projects/battsimpy/'
#bsp_dir = '/home/mk-sim-linux/Battery_TempGrad/Python/batt_simulation/battsimpy/'
#bsp_dir = '/Users/mk/Desktop/battsim/battsimpy/'

uref_a_map = numpy.loadtxt( bsp_dir + '/data/Model_v1/Model_Pars/solid/thermodynamics/uref_anode_x.csv'  , delimiter=',' )
uref_c_map = numpy.loadtxt( bsp_dir + '/data/Model_v1/Model_Pars/solid/thermodynamics/uref_cathode_x.csv', delimiter=',' )

if uref_a_map[1,0] > uref_a_map[0,0] :
    uref_a_interp = scipy.interpolate.interp1d( uref_a_map[:,0], uref_a_map[:,1] )
else :
    uref_a_interp = scipy.interpolate.interp1d( numpy.flipud(uref_a_map[:,0]), numpy.flipud(uref_a_map[:,1]) )

if uref_c_map[1,0] > uref_c_map[0,0] :
    uref_c_interp = scipy.interpolate.interp1d( uref_c_map[:,0], uref_c_map[:,1] )
else :
    uref_c_interp = scipy.interpolate.interp1d( numpy.flipud(uref_c_map[:,0]), numpy.flipud(uref_c_map[:,1]) )

xa_init, xc_init = 0.8, 0.37
ca_init = xa_init*csa_max 
cc_init = xc_init*csc_max
Ua_init = uref_a_interp( xa_init )
Uc_init = uref_c_interp( xc_init )

print Ua_init
print Uc_init

### Mesh
La = 65.0
Ls = 25.0
Lc = 55.0
Lt = (La+Ls+Lc)
X = Lt*1e-6 # [m]

N = 80
Ns = int(N*(Ls/Lt))
Na = int(N*(La/Lt))
Nc = N - Ns - Na


Crate = 0.5
Vcut  = 3.0 # [V], cutoff voltage for end of discharge
ce_lims = [100.,3000.]

cell_cap = 29.0
cell_coated_area = 1.0 # [m^2]

I_app = Crate*cell_cap # A

### Initial conditions
# E-lyte conc
c_init = 1100.0 # [mol/m^3]
c_centered = c_init*numpy.ones( N, dtype='d' ) #numpy.linspace(1500, 500, N) #
# E-lyte potential
p_init = 0.0 # [V]
p_centered = p_init*numpy.ones( N, dtype='d' )
# Solid potential on anode and cathode
pa_init = Ua_init #0.0 # [V]
pa_centered = pa_init*numpy.ones( Na, dtype='d' )
pc_init = Uc_init#-Ua_init #0.0 # [V]
pc_centered = pc_init*numpy.ones( Nc, dtype='d' )
# Solid conc on anode and cathode
#ca_init = 10000.0 # [mol/m^3]
ca_centered = ca_init*numpy.ones( Na, dtype='d' )
#cc_init = 30000.0 # [mol/m^3]
cc_centered = cc_init*numpy.ones( Nc, dtype='d' )

tv = [ 15.0+i*5. for i in range(5) ]
Tvec = [ 15.0+273.15+i*5. for i in range(5) ]

ja = numpy.zeros(Na)
jc = numpy.zeros(Nc)

#The initial conditons
y0  = [ numpy.concatenate( [c_centered, ca_centered, cc_centered, [T], ja, jc, p_centered, pa_centered, pc_centered] ) for T in Tvec ]#Initial conditions
yd0 = [0.0 for i in range(len(y0[0]))] #Initial conditions

num_diff_vars = len( numpy.concatenate( [c_centered, ca_centered, cc_centered, [Tvec[0]]] ) )
num_algr_vars = len(y0[0]) - num_diff_vars

#Create an Assimulo implicit problem
imp_mod = [ MyProblem(Na,Ns,Nc,X,cell_coated_area,bsp_dir,y0_i,yd0,'anyl jac') for y0_i in y0 ]

#Sets the options to the problem
for im in imp_mod :
    im.algvar = [1.0 for i in range(num_diff_vars)] + [0.0 for i in range(num_algr_vars)] #Set the algebraic components

#Create an Assimulo implicit solver (IDA)
imp_sim = [ IDA(im) for im in imp_mod ] #Create a IDA solver

#Sets the paramters
for ims in imp_sim :
    ims.atol = 1e-5 #Default 1e-6
    ims.rtol = 1e-5 #Default 1e-6
    ims.suppress_alg = True #Suppres the algebraic variables on the error test

    ims.display_progress = False
    ims.verbosity = 50
    ims.report_continuously = True
    ims.time_limit = 10.


### Simulate
#imp_mod.set_iapp( I_app/10. )
#imp_sim.make_consistent('IDA_YA_YDP_INIT')
#ta, ya, yda = imp_sim.simulate(0.1,5) 
##
#imp_mod.set_iapp( I_app/2. )
#imp_sim.make_consistent('IDA_YA_YDP_INIT')
#tb, yb, ydb = imp_sim.simulate(0.2,5) 

#imp_mod.set_iapp( I_app )
#imp_sim.make_consistent('IDA_YA_YDP_INIT')
## Sim step 1
#t1, y1, yd1 = imp_sim.simulate(1./Crate*3600.*0.2,100) 


### Simulate
t01, t02 = 0.1, 0.2

ta = [ 0 for imod in range(len(Tvec)) ]
tb = [ 0 for imod in range(len(Tvec)) ]
ya = [ 0 for imod in range(len(Tvec)) ]
yb = [ 0 for imod in range(len(Tvec)) ]
yda = [ 0 for imod in range(len(Tvec)) ]
ydb = [ 0 for imod in range(len(Tvec)) ]

for i, imod in enumerate( imp_mod ) :
    isim = imp_sim[i]

    imod.set_iapp( I_app/10. )
    isim.make_consistent('IDA_YA_YDP_INIT')
    ta[i], ya[i], yda[i] = isim.simulate(t01,2) 

    imod.set_iapp( I_app/2. )
    isim.make_consistent('IDA_YA_YDP_INIT')
    tb[i], yb[i], ydb[i] = isim.simulate(t02,2) 

print 'yb[0] shape', yb[0].shape

# Sim step 1
#t1 = [ 0 for imod in range(len(Tvec)) ]
#y1 = [ 0 for imod in range(len(Tvec)) ]
#yd1 = [ 0 for imod in range(len(Tvec)) ]

#for i, imod in enumerate( imp_mod ) :
#    imod.set_iapp( I_app )
#    isim.make_consistent('IDA_YA_YDP_INIT')
#    t1[i], y1[i], yd1[i] = imp_sim[i].simulate(1.0/Crate*3600.0*0.1,20) 

NT = 80
time   = numpy.linspace( t02+0.1, 1.0/Crate*3600.0, NT )#numpy.linspace( t02+0.1, 60., NT ) #
t_out  = [ 0 for ts in time ]
V_out  = [ [ 0 for ts in time ] for imod in range(len(Tvec)) ]
T_out  = [ [ 0 for ts in time ] for imod in range(len(Tvec)) ]
I_out  = [ [ 0 for ts in time ] for imod in range(len(Tvec)) ]
y_out  = [ numpy.zeros( [len(time), yb[imod].shape[ 1]] ) for imod in range(len(Tvec)) ]
yd_out = [ numpy.zeros( [len(time), ydb[imod].shape[1]] ) for imod in range(len(Tvec)) ]

#print 'y_out.shape', y_out.shape

it = 0
V_cell = [ imp_mod[i].get_voltage( yb[i][-1,:].flatten() ) for i in range(len(Tvec)) ]
ce_now = [ yb[i][-1,imp_mod[i].ce_inds].flatten() for i in range(len(Tvec)) ]
print 'V_cell prior to time loop:', min(V_cell)

for i, imod in enumerate( imp_mod ) :
    imod.set_iapp( I_app )
    imp_sim[i].make_consistent('IDA_YA_YDP_INIT')

yi, ydi = [ 0 for i in range(len(imp_mod)) ], [ 0 for i in range(len(imp_mod)) ]

sim_stopped = 0

#Vcut = min(V_cell)*0.9

Nsub = 10
Vtol = 0.0002

Ivec = numpy.array([I_app for imod in imp_mod])

while min(V_cell) > Vcut and numpy.amax(numpy.array(ce_now))<max(ce_lims) and numpy.amin(numpy.array(ce_now))>min(ce_lims) and not sim_stopped and it<len(time) :

    isub = 0
    Vdiff = 2.*Vtol
    while Vdiff > Vtol and isub < Nsub :
        for im, imod in enumerate(imp_mod) :
            I_out[im][it] = Ivec[im]
        try :
            for im, imod in enumerate(imp_mod) :
                ti, yi[im], ydi[im] = imp_sim[im].simulate(time[it],1)
        except :
            for im in range(len(imp_mod)) :
                ti  = [t_out[it-1],t_out[it-1]]
                yi[im]  = y_out[im][ it-2:it,:]
                ydi[im] = yd_out[im][ it-2:it,:]

            sim_stopped = 1

            print 'Sim stopped due time integration failure.'

        t_out[ it]   = ti[ -1  ]
        print t_out[it]
        for im, imod in enumerate( imp_mod ) :
            y_out[im][ it,:] = yi[im][ -1,:]
            yd_out[im][it,:] = ydi[im][-1,:]

            V_cell[im] = imod.get_voltage( y_out[im][it,:] )

            V_out[im][it] = V_cell[im]

            T_out[im][it] = y_out[im][it,imod.T_ind]

            ce_now[im] = y_out[im][it,imod.ce_inds]

#            print 'V_cell[im]',V_cell[im]

#            print 'time:',round(t_out[it],3), ' |  Voltage:', round(V_cell[im],3)

        Vdiff = numpy.amax(V_cell) - numpy.amin(V_cell)
        
        Vmean = numpy.mean( V_cell )
        Verr = numpy.array(V_cell) - Vmean
        
        print 'Ivec_pre', Ivec
        Ivec = Ivec + Verr*120.
        print 'Ivec', Ivec
        print 'V_cell', V_cell
        print 'Verr', Verr
        for i, imod in enumerate( imp_mod ) :
            imod.set_iapp( Ivec[i] )
            imp_sim[i].make_consistent('IDA_YA_YDP_INIT')

        isub+=1

    if min(V_cell) < Vcut :
        print '\n','Vcut stopped simulation.'
    elif numpy.amax(numpy.array(ce_now))>max(ce_lims) :
        print '\n','ce max stopped simulation.'
    elif numpy.amin(numpy.array(ce_now))<min(ce_lims) :
        print '\n','ce min stopped simulation.'

    it+=1

ce = [ numpy.zeros_like(t_out) for i in imp_mod ]
if it < len(time) :
    t_out  = t_out[ :it  ]
    for i, imod in enumerate( imp_mod ) :
        I_out[i]  = I_out[i][ :it  ]
        V_out[i]  = V_out[i][ :it  ]
        T_out[i]  = T_out[i][ :it  ]
        y_out[i]  = y_out[i][ :it,:]
        yd_out[i] = yd_out[i][:it,:]

for i, imod in enumerate( imp_mod ) :
    ce[i] = y_out[i][:,imod.ce_inds]


f, ax = plt.subplots( 1,2 )
axI = ax[1].twinx()
clr = [ 'b', 'c', 'g', 'm', 'r', 'k' ]
for i, imod in enumerate(imp_mod) :
    ax[0].plot( imod.x_m, ce[i].T, color=clr[i], label='T:'+str(Tvec[i]) )
    ax[1].plot( t_out, V_out[i], color=clr[i], label='T:'+str(Tvec[i]) )
    axI.plot( t_out, I_out[i], color=clr[i], linestyle='--', label='T:'+str(Tvec[i]) )
    axI.plot( t_out, numpy.array(T_out[i])-273.15, color=clr[i], linestyle=':', label='T:'+str(Tvec[i]) )
plt.show()


#t1  = t_out
#y1  = y_out
#yd1 = yd_out

#im = imp_mod
#Q_out_1 = numpy.zeros_like(t1)
#Q_in_1  = numpy.zeros_like(t1)
#Res_1  = numpy.zeros_like(t1)
#ResQ_1  = numpy.zeros_like(t1)
#for it, t in enumerate( t1 ) :

#    y = y1[it,:]

#    ce     = y[ im.ce_inds]
#    # Solid conc a:anode, c:cathode
#    csa    = y[ im.csa_inds]
#    csc    = y[ im.csc_inds]

#    # Reaction (Butler-Volmer Kinetics)
#    ja_rxn = y[im.ja_inds]
#    jc_rxn = y[im.jc_inds]

#    # E-lyte potential
#    phi = y[im.pe_inds]

#    # Solid potential
#    phi_s_a = y[im.pa_inds]
#    phi_s_c = y[im.pc_inds]

#    T = y[im.T_ind]

#    csa_mean = numpy.mean(csa)
#    csc_mean = numpy.mean(csc)
#    Uam = im.uref_a_interp( csa_mean/im.csa_max )
#    Ucm = im.uref_c_interp( csc_mean/im.csc_max )

#    V = im.get_voltage( y )

#    if im.i_app != 0. :
#        Res_1[it] = ((Ucm-Uam) - V)/(im.i_app*im.Ac)

#    eta_a, eta_c, Uref_a, Uref_c, csa_ss, csc_ss = im.get_eta_uref( csa, csc, ja_rxn, jc_rxn, phi_s_a, phi_s_c, phi )

#    Q_in_1[it] = imp_mod.calc_heat( y, eta_a, eta_c, Uref_a, Uref_c )

#    Q_out_1[it] = 1./im.h/im.Aconv * (T-im.T_amb)

#    if im.i_app != 0. :
#        ResQ_1[it] = Q_in_1[it]/(im.i_app**2)



#imp_mod.set_iapp( 0.0 )
#imp_sim.make_consistent('IDA_YA_YDP_INIT')
## Sim step 1
#t2, y2, yd2 = imp_sim.simulate(t1[-1]*1.5,100) 


#Q_out_2 = numpy.zeros_like(t2)
#Q_in_2  = numpy.zeros_like(t2)
#Res_2  = numpy.zeros_like(t2)
#ResQ_2  = numpy.zeros_like(t2)
#for it, t in enumerate( t2 ) :

#    y = y2[it,:]

#    ce     = y[ im.ce_inds]
#    # Solid conc a:anode, c:cathode
#    csa    = y[ im.csa_inds]
#    csc    = y[ im.csc_inds]

#    # Reaction (Butler-Volmer Kinetics)
#    ja_rxn = y[im.ja_inds]
#    jc_rxn = y[im.jc_inds]

#    # E-lyte potential
#    phi = y[im.pe_inds]

#    # Solid potential
#    phi_s_a = y[im.pa_inds]
#    phi_s_c = y[im.pc_inds]

#    T = y[im.T_ind]

#    csa_mean = numpy.mean(csa)
#    csc_mean = numpy.mean(csc)
#    Uam = im.uref_a_interp( csa_mean/im.csa_max )
#    Ucm = im.uref_c_interp( csc_mean/im.csc_max )

#    V = im.get_voltage( y )

#    if im.i_app != 0. :
#        Res_2[it] = ((Ucm-Uam) - V)/(im.i_app*im.Ac)

#    eta_a, eta_c, Uref_a, Uref_c, csa_ss, csc_ss = im.get_eta_uref( csa, csc, ja_rxn, jc_rxn, phi_s_a, phi_s_c, phi )

#    Q_in_2[it] = imp_mod.calc_heat( y, eta_a, eta_c, Uref_a, Uref_c )

#    Q_out_2[it] = 1./im.h/im.Aconv * (T-im.T_amb)

#    if im.i_app != 0. :
#        ResQ_2[it] = Q_in_2[it]/(im.i_app**2)



## extract variables
#im = imp_mod
#ce_1 = y1[:,im.ce_inds]
#ca_1 = y1[:,im.csa_inds]
#cc_1 = y1[:,im.csc_inds]

#pe_1 = y1[:,im.pe_inds]
#pa_1 = y1[:,im.pa_inds]
#pc_1 = y1[:,im.pc_inds]

#ja_1 = y1[:,im.ja_inds]
#jc_1 = y1[:,im.jc_inds]

#T_1 = y1[:,im.T_ind]

#ce_2 = y2[:,im.ce_inds]
#ca_2 = y2[:,im.csa_inds]
#cc_2 = y2[:,im.csc_inds]

#pe_2 = y2[:,im.pe_inds]
#pa_2 = y2[:,im.pa_inds]
#pc_2 = y2[:,im.pc_inds]

#ja_2 = y2[:,im.ja_inds]
#jc_2 = y2[:,im.jc_inds]

#T_2 = y2[:,im.T_ind]

#Jsum_a1 = numpy.array( [ sum(imp_mod.vols_a*imp_mod.F*imp_mod.as_a*ja_1[i,:]) for i in range(len(ja_1[:,0])) ] )
#Jsum_c1 = numpy.array( [ sum(imp_mod.vols_c*imp_mod.F*imp_mod.as_c*jc_1[i,:]) for i in range(len(jc_1[:,0])) ] )

#plt.figure()
#plt.plot( t1, Jsum_a1-Jsum_c1 )


##Plot
## t1
## Plot through space
#f, ax = plt.subplots(2,5)
## ce vs x
#ax[0,0].plot(imp_mod.x_m*1e6,ce_1.T) 
## pe vs x
#ax[0,1].plot(imp_mod.x_m*1e6,pe_1.T)
## pa vs x
#ax[0,2].plot(imp_mod.x_m_a*1e6,pa_1.T)
## pc vs x
#ax[0,2].plot(imp_mod.x_m_c*1e6,pc_1.T)
## ca vs x
#ax[0,3].plot(imp_mod.x_m_a*1e6,ca_1.T)
## cc vs x
#ax[0,3].plot(imp_mod.x_m_c*1e6,cc_1.T)
## ja vs x
#ax[0,4].plot(imp_mod.x_m_a*1e6,ja_1.T)
## jc vs x
#ax[0,4].plot(imp_mod.x_m_c*1e6,jc_1.T)

#ax[0,0].set_title('t1 c')
#ax[0,0].set_xlabel('Cell Thickness [$\mu$m]')
#ax[0,0].set_ylabel('E-lyte Conc. [mol/m$^3$]')
#ax[0,1].set_title('t1 p')
#ax[0,1].set_xlabel('Cell Thickness [$\mu$m]')
#ax[0,1].set_ylabel('E-lyte Potential [V]')
#ax[0,2].set_title('t1 p solid')
#ax[0,2].set_xlabel('Cell Thickness [$\mu$m]')
#ax[0,2].set_ylabel('Solid Potential [V]')
#ax[0,3].set_title('t1 conc solid')
#ax[0,3].set_xlabel('Cell Thickness [$\mu$m]')
#ax[0,3].set_ylabel('Solid Conc. [mol/m$^3$]')

## t2
#ax[1,0].plot(imp_mod.x_m*1e6,ce_2.T)
#ax[1,1].plot(imp_mod.x_m*1e6,pe_2.T)

#ax[1,2].plot(imp_mod.x_m_a*1e6,pa_2.T)
#ax[1,2].plot(imp_mod.x_m_c*1e6,pc_2.T)

#ax[1,3].plot(imp_mod.x_m_a*1e6,ca_2.T)
#ax[1,3].plot(imp_mod.x_m_c*1e6,cc_2.T)

#ax[1,4].plot(imp_mod.x_m_a*1e6,ja_2.T)
#ax[1,4].plot(imp_mod.x_m_c*1e6,jc_2.T)

#ax[1,0].set_title('t2 c')
#ax[1,0].set_xlabel('Cell Thickness [$\mu$m]')
#ax[1,0].set_ylabel('E-lyte Conc. [mol/m$^3$]')
#ax[1,1].set_title('t2 p e-lyte')
#ax[1,1].set_xlabel('Cell Thickness [$\mu$m]')
#ax[1,1].set_ylabel('E-lyte Potential [V]')
#ax[1,2].set_title('t2 p solid')
#ax[1,2].set_xlabel('Cell Thickness [$\mu$m]')
#ax[1,2].set_ylabel('Solid Potential [V]')
#ax[1,3].set_title('t2 Solid Conc.')
#ax[1,3].set_xlabel('Cell Thickness [$\mu$m]')
#ax[1,3].set_ylabel('Solid Conc. [mol/m$^3$]')

#plt.tight_layout()

## Plot through time
#f, ax = plt.subplots(1,4)
#ax[0].plot(t1,ce_1)
#ax[1].plot(t1,pe_1)
#ax[2].plot(t1,pa_1) 
#ax[2].plot(t1,pc_1)
#ax[3].plot(t1,ca_1) 
#ax[3].plot(t1,cc_1) 

#ax[0].plot(t2,ce_2)
#ax[1].plot(t2,pe_2)
#ax[2].plot(t2,pa_2) 
#ax[2].plot(t2,pc_2) 
#ax[3].plot(t2,ca_2) 
#ax[3].plot(t2,cc_2) 

#ax[0].set_ylabel('E-lyte Conc. [mol/m$^3$]')
#ax[0].set_xlabel('Time [s]')
#ax[1].set_ylabel('E-lyte Potential [V]')
#ax[1].set_xlabel('Time [s]')
#ax[2].set_ylabel('Solid Potential [V]')
#ax[2].set_xlabel('Time [s]')
#ax[3].set_ylabel('Solid Conc. [mol/m$^3$]')
#ax[3].set_xlabel('Time [s]')

#plt.tight_layout()

#f, ax = plt.subplots(1,2)
#ax[0].plot( t1, pc_1[:,-1] - pa_1[:,0] )
#ax[0].plot( t2, pc_2[:,-1] - pa_2[:,0] )

#ax[1].plot( t1, T_1, label='T1' )
#ax[1].plot( t2, T_2, label='T2' )
#ax[1].set_ylim( [290,310] )

#ax2 = ax[1].twinx()
#ax2.plot( t1, Q_in_1, label='in 1' )
#ax2.plot( t1, Q_out_1, label='out 1' )
#ax2.plot( t2, Q_in_2, label='in 2' )
#ax2.plot( t2, Q_out_2, label='out 2' )
#ax[1].legend(loc=1)
#ax2.legend(loc=2)

#plt.figure()
#plt.plot( t1, Res_1, label='R 1' )
#plt.plot( t1, ResQ_1, label='RQ 1' )
#plt.plot( t2, Res_2, label='R 2' )
#plt.plot( t2, ResQ_2, label='RQ 2' )
#plt.legend()

#plt.show()





#
#
### main call
#
#imp_mod = MyProblem(Na,Ns,Nc,X,cell_coated_area,bsp_dir,y0,yd0,'Example using an analytic Jacobian')
#
## my own time solver
#
#delta_t = 10.0
#tf = 2000.
#time = [ i*delta_t for i in range(int(tf/delta_t)+1) ]
#
#print time
#
#x_out = numpy.zeros( [imp_mod.num_diff_vars, len(time)] )
#z_out = numpy.zeros( [imp_mod.num_algr_vars, len(time)] )
#
#x_out[:,0] = numpy.concatenate( [c_centered, ca_centered, cc_centered, [T]] )
#z_out[:,0] = numpy.concatenate( [ja, jc, p_centered, pa_centered, pc_centered] )
#
#V_out = numpy.zeros_like(time)
#V_out[0] = imp_mod.get_voltage( numpy.concatenate( [x_out[:,0], z_out[:,0]] )     )
#
#for it, t in enumerate(time[1:]) :
#
#    if it == 0 :
#        Cur_vec = [ 0.0, 0.0, 0.1*I_app ]
#    elif it == 1 :
#        Cur_vec = [ 0.0, 0.1*I_app, 0.5*I_app ]
#    elif it == 2 :
#        Cur_vec = [ 0.1*I_app, 0.5*I_app, I_app ]
#    else :
#        Cur_vec = [ I_app, I_app, I_app ]
#        
#    x_out[:,it+1], z_out[:,it+1], newtonStats = imp_mod.cn_solver( x_out[:,it], z_out[:,it], Cur_vec, delta_t )
#    
#    print 'Anode io:',imp_mod.C_ioa[0]*imp_mod.F, 'Cathode io:',imp_mod.C_ioc[0]*imp_mod.F
#    
#    ynew = numpy.concatenate( [x_out[:,it+1], z_out[:,it+1]] )    
#    V_out[it+1] = imp_mod.get_voltage( ynew )
#    
#
#plt.close()
#f, ax = plt.subplots(1,3)
#ax[0].plot( imp_mod.x_m, x_out[:imp_mod.N] )
#
#ax[1].plot( imp_mod.x_m, z_out[imp_mod.Na+imp_mod.Nc:imp_mod.Na+imp_mod.Nc+imp_mod.N,:-1] )
#
#ax[2].plot( imp_mod.x_m_a, z_out[-imp_mod.Na-imp_mod.Nc:-imp_mod.Nc,:-1] )
#ax[2].plot( imp_mod.x_m_c, z_out[-imp_mod.Nc:,:-1] )
#
#f2, ax = plt.subplots(1,2)
#ax[0].plot(time, V_out)
#ax[1].plot(time, x_out[-1,:])
#
#plt.show()
#
#print z_out
#
#





#
#
#
#    def dae_system_num( self, y ) :
#
#        self.set_iapp( self.Input )
#
#        ## Parse out the states
#        # E-lyte conc
#        ce     = y[ self.ce_inds]
#
#        # Solid conc a:anode, c:cathode
#        csa    = y[ self.csa_inds]
#        csc    = y[ self.csc_inds]
#
#        # Reaction (Butler-Volmer Kinetics)
#        ja_rxn = y[self.ja_inds]
#        jc_rxn = y[self.jc_inds]
#
#        # E-lyte potential
#        phi = y[self.pe_inds]
#
#        # Solid potential
#        phi_s_a = y[self.pa_inds]
#        phi_s_c = y[self.pc_inds]
#
#        # Temp
#        T = y[self.T_ind]
#
#        ## Grab state dependent matrices
#        # For E-lyte conc and potential (i.e., De(ce), kapp_e(ce))
#        A_ce = self.build_Ace_mat( ce, T )
#        A_pe = self.build_Ape_mat( ce, T )
#        B_pe = self.build_Bpe_mat( ce, T )
#
#
#        ## Compute extra variables
#        # For the reaction kinetics
#        eta_a, eta_c, Uref_a, Uref_c, csa_ss, csc_ss = self.get_eta_uref( csa, csc, ja_rxn, jc_rxn, phi_s_a, phi_s_c, phi )
#
#        # For kinetics, the io param is now conc dependent
#        self.update_Cio( csa_ss, csc_ss, ce, T )
#
#        Q_in = self.calc_heat( y, eta_a, eta_c, Uref_a, Uref_c )
#
#        Q_out = (self.h*self.Aconv)*(T - self.T_amb)
#
#        ja = self.C_ioa * numpy.sinh( 0.5*self.F/(self.R_gas*T)*eta_a )
#        jc = self.C_ioc * numpy.sinh( 0.5*self.F/(self.R_gas*T)*eta_c )
#
#        j = numpy.concatenate( [ ja_rxn, numpy.zeros(self.Ns), jc_rxn ] )
#
#        ## Compute the residuals
#        # Time deriv components
#        r1 = ( ((A_ce.dot(ce)).flatten() + (self.B_ce.dot(j)).flatten()) ) # E-lyte conc
#
#        r2 = (self.B_cs_a.dot(ja_rxn).flatten()) # Anode   conc
#        r3 = (self.B_cs_c.dot(jc_rxn).flatten()) # Cathode conc        
#
#        r4 = 1./(self.rho*self.Cp)*( Q_in - Q_out )
#
#        # Algebraic components
#        r5 = ja_rxn - ja
#        r6 = jc_rxn - jc 
#
#        r7 = A_pe.dot(phi).flatten() - B_pe.dot(ce).flatten() + self.B2_pe.dot(j).flatten() # E-lyte potential
#
#        r8 = self.A_ps_a.dot(phi_s_a).flatten() - self.B_ps_a.dot(ja_rxn).flatten() - self.B2_ps_a*self.i_app # Anode   potential #+ extra #
#        r9 = self.A_ps_c.dot(phi_s_c).flatten() - self.B_ps_c.dot(jc_rxn).flatten() + self.B2_ps_c*self.i_app # Cathode potential
#
#        res_out = numpy.concatenate( [r1,r2,r3, [r4], r5, r6, r7, r8, r9] )
#
#        return res_out
#
#
#
#    def dae_system( self, x, z, Input, get_mats=0 ) :
#
#        self.set_iapp( Input )
#
#        y = numpy.concatenate([x,z])
#
#        ## Parse out the states
#        # E-lyte conc
#        ce     = y[ self.ce_inds]
#
#        # Solid conc a:anode, c:cathode
#        csa    = y[ self.csa_inds]
#        csc    = y[ self.csc_inds]
#
#        # Reaction (Butler-Volmer Kinetics)
#        ja_rxn = y[self.ja_inds]
#        jc_rxn = y[self.jc_inds]
#
#        # E-lyte potential
#        phi = y[self.pe_inds]
#
#        # Solid potential
#        phi_s_a = y[self.pa_inds]
#        phi_s_c = y[self.pc_inds]
#
#        # Temp
#        T = y[self.T_ind]
#
#        ## Grab state dependent matrices
#        # For E-lyte conc and potential (i.e., De(ce), kapp_e(ce))
#        A_ce = self.build_Ace_mat( ce, T )
#        A_pe = self.build_Ape_mat( ce, T )
#        B_pe = self.build_Bpe_mat( ce, T )
#
#
#        ## Compute extra variables
#        # For the reaction kinetics
#        eta_a, eta_c, Uref_a, Uref_c, csa_ss, csc_ss = self.get_eta_uref( csa, csc, ja_rxn, jc_rxn, phi_s_a, phi_s_c, phi )
#
#        xa = csa/self.csa_max
#        xc = csc/self.csc_max
#
#        xa_ss = csa_ss/self.csa_max
#        xc_ss = csc_ss/self.csc_max
#
#        # For kinetics, the io param is now conc dependent
#        self.update_Cio( csa_ss, csc_ss, ce, T )
#
#        Q_in = self.calc_heat( y, eta_a, eta_c, Uref_a, Uref_c )
#
#        Q_out = (self.h*self.Aconv)*(T - self.T_amb)
#
#        ja = self.C_ioa * numpy.sinh( 0.5*self.F/(self.R_gas*T)*eta_a )
#        jc = self.C_ioc * numpy.sinh( 0.5*self.F/(self.R_gas*T)*eta_c )
#
#        j = numpy.concatenate( [ ja_rxn, numpy.zeros(self.Ns), jc_rxn ] )
#
#        ## Compute the residuals
#        # Time deriv components
#        r1 = ( ((A_ce.dot(ce)).flatten() + (self.B_ce.dot(j)).flatten()) ) # E-lyte conc
#
#        r2 = (self.B_cs_a.dot(ja_rxn).flatten()) # Anode   conc
#        r3 = (self.B_cs_c.dot(jc_rxn).flatten()) # Cathode conc        
#
#        r4 = 1./(self.rho*self.Cp)*( Q_in - Q_out )
#
#        # Algebraic components
#        r5 = ja_rxn - ja
#        r6 = jc_rxn - jc 
#
#        r7 = A_pe.dot(phi).flatten() - B_pe.dot(ce).flatten() + self.B2_pe.dot(j).flatten() # E-lyte potential
#
#        r8 = self.A_ps_a.dot(phi_s_a).flatten() - self.B_ps_a.dot(ja_rxn).flatten() - self.B2_ps_a*self.i_app # Anode   potential #+ extra #
#        r9 = self.A_ps_c.dot(phi_s_c).flatten() - self.B_ps_c.dot(jc_rxn).flatten() + self.B2_ps_c*self.i_app # Cathode potential
#
#        if get_mats :
#            res_out = numpy.concatenate( [r1,r2,r3,[r4]] ), numpy.concatenate( [r5, r6, r7, r8, r9] ), { 'A_ce':A_ce, 'A_pe':A_pe, 'B_pe':B_pe, 'csa':csa, 'csc':csc, 'csa_ss':csa_ss, 'csc_ss':csc_ss, 'xa':xa, 'xc':xc, 'xa_ss':xa_ss, 'xc_ss':xc_ss, 'eta_a':eta_a, 'eta_c':eta_c, 'T':T, 'ce':ce, 'phi':phi }
#        else :
#            res_out = numpy.concatenate( [r1,r2,r3,[r4]] ), numpy.concatenate( [r5, r6, r7, r8, r9] )
#
#        return res_out
#
#
#    def jac_system( self, mats ) :
#
#        dcss_dcs_a = 1.0
#        dcss_dcs_c = 1.0
#
#        dcss_dja = numpy.diagonal( self.D_cs_a )
#        dcss_djc = numpy.diagonal( self.D_cs_c )
#
#        ### Build the Jac matrix
#        ## Self coupling
#        Bjac_a = self.build_Bjac_mat( mats['eta_a'], self.C_ioa, 0.5*self.F/(self.R_gas*mats['T']) )
#        Bjac_c = self.build_Bjac_mat( mats['eta_c'], self.C_ioc, 0.5*self.F/(self.R_gas*mats['T']) )
#
#        BjT_a = self.build_BjT_mat( mats['T'], self.C_ioa, 0.5*self.F/(self.R_gas)*mats['eta_a'] )
#        BjT_c = self.build_BjT_mat( mats['T'], self.C_ioc, 0.5*self.F/(self.R_gas)*mats['eta_c'] )
#
#        dU_csa_ss = (1.0/self.csa_max)*self.duref_a_interp(mats['xa_ss'])
#        dU_csc_ss = (1.0/self.csc_max)*self.duref_c_interp(mats['xc_ss'])
#
#        DUDcsa_ss = numpy.diag( dU_csa_ss )
#        DUDcsc_ss = numpy.diag( dU_csc_ss )
#
#        A_ce = mats['A_ce'] #self.build_Ace_mat( ce )
#        A_pe = mats['A_pe'] #self.build_Ape_mat( ce )
#        B_pe = mats['B_pe'] #self.build_Bpe_mat( ce )
#
#        DUDcsa_ss = numpy.diag( dU_csa_ss )
#        DUDcsc_ss = numpy.diag( dU_csc_ss )
#
#        Bja = Bjac_a.dot(-1.0*DUDcsa_ss.dot(self.D_cs_a))
#        Bjc = Bjac_c.dot(-1.0*DUDcsc_ss.dot(self.D_cs_c))
#
#        A_ja = numpy.diag(numpy.ones(self.Na)) - Bja
#        A_jc = numpy.diag(numpy.ones(self.Nc)) - Bjc
#
#        ##
#        fx =  scipy.linalg.block_diag( A_ce, numpy.zeros([self.Na,self.Na]), numpy.zeros([self.Nc,self.Nc]), [-self.h*self.Aconv/self.rho/self.Cp] )
#
#        # T vs csa and csc
#        fx[self.T_ind,self.csa_inds] = 1./(self.rho*self.Cp)*(2.0*(self.C_q_na*(-1.0)*dU_csa_ss*dcss_dcs_a))
#        fx[self.T_ind,self.csc_inds] = 1./(self.rho*self.Cp)*(2.0*(self.C_q_nc*(-1.0)*dU_csc_ss*dcss_dcs_c))
#        ##
#
#        ##
#        fz =  numpy.zeros( [self.num_diff_vars, self.num_algr_vars] )
#
#        # ce vs j
#        fz[ numpy.ix_(range(self.N), range(self.Na)) ] = self.B_ce[:, :self.Na]
#        fz[ numpy.ix_(range(self.N), range(self.Na,self.Na+self.Nc)) ] = self.B_ce[:, -self.Nc:]
#
#        # cs vs j
#        fz[ numpy.ix_(range(self.N,self.N+self.Na), range(self.Na)) ] = self.B_cs_a
#        fz[ numpy.ix_(range(self.N+self.Na,self.N+self.Na+self.Nc), range(self.Na,self.Na+self.Nc)) ] = self.B_cs_c
#
#        # T vs all z vars
#        fz[self.T_ind,self.ja_inds2]  = 1./(self.rho*self.Cp)*(self.C_q_ja + 2.0*(self.C_q_na*(-1.0)*dU_csa_ss*dcss_dja))
#        fz[self.T_ind,self.jc_inds2]  = 1./(self.rho*self.Cp)*(self.C_q_jc + 2.0*(self.C_q_nc*(-1.0)*dU_csc_ss*dcss_djc))
#        fz[self.T_ind,self.pe_inds2]  = 1./(self.rho*self.Cp)*(self.C_q_pe + numpy.array( list(self.C_q_na)+[0. for i in range(self.Ns)]+list(self.C_q_nc) )*(-1.0))
#        fz[self.T_ind,self.pa_inds2]  = 1./(self.rho*self.Cp)*(self.C_q_pa + self.C_q_na*(1.0))
#        fz[self.T_ind,self.pc_inds2]  = 1./(self.rho*self.Cp)*(self.C_q_pc + self.C_q_nc*(1.0))
#
#        ##
#
#        ##
#        gx =  numpy.zeros( [self.num_algr_vars, self.num_diff_vars] )
#
#        # j vs cs_bar
#        gx[numpy.ix_(range(self.Na),range(self.N,self.N+self.Na))] = -Bjac_a.dot(-1.0*DUDcsa_ss*1.0)
#        gx[numpy.ix_(range(self.Na,self.Na+self.Nc),range(self.N+self.Na,self.N+self.Na+self.Nc))] = -Bjac_c.dot(-1.0*DUDcsc_ss*1.0)
#
#        # j vs T
#        gx[self.ja_inds2,self.T_ind] = -BjT_a
#        gx[self.jc_inds2,self.T_ind] = -BjT_c
#
#        # phi_e vs ce
#        gx[numpy.ix_(range(self.Na+self.Nc,self.Na+self.Nc+self.N),range(self.N))] = -B_pe
#
#        ##
#
#        ##
#        # z vs z
#        gz0 =  scipy.linalg.block_diag( A_ja, A_jc, A_pe, self.A_ps_a, self.A_ps_c )
#
#        # z cross coupling
#        gz00 = numpy.zeros_like( gz0 )
#        # phi_e vs j
#        gz00[ numpy.ix_(range(self.Na+self.Nc,self.Na+self.Nc+self.N),range(self.Na)) ] = self.B2_pe[:,:self.Na]
#        gz00[ numpy.ix_(range(self.Na+self.Nc,self.Na+self.Nc+self.N),range(self.Na,self.Na+self.Nc)) ] = self.B2_pe[:,-self.Nc:]
#
#        # phi_s vs j
#        gz00[ numpy.ix_(range(self.Na+self.Nc+self.N, self.Na+self.Nc+self.N +self.Na),range(self.Na)) ] = -self.B_ps_a
#        gz00[ numpy.ix_(range(self.Na+self.Nc+self.N+self.Na,self.Na+self.Nc+self.N+self.Na+self.Nc),range(self.Na,self.Na+self.Nc)) ] = -self.B_ps_c
#
#        # j vs phi_s
#        gz00[ numpy.ix_(range(self.Na), range(self.Na+self.Nc+self.N,self.Na+self.Nc+self.N+self.Na)) ] = -Bjac_a*( 1.0)
#        gz00[ numpy.ix_(range(self.Na,self.Na+self.Nc),range(self.Na+self.Nc+self.N+self.Na,self.Na+self.Nc+self.N+self.Na+self.Nc)) ] = -Bjac_c*( 1.0)
#
#        # j vs phi_e
#        gz00[ numpy.ix_(range(self.Na), range(self.Na+self.Nc,self.Na+self.Nc+self.Na)) ] = -Bjac_a*(-1.0)
#        gz00[ numpy.ix_(range(self.Na,self.Na+self.Nc), range(self.Na+self.Nc+self.Na+self.Ns,self.Na+self.Nc+self.N)) ] = -Bjac_c*(-1.0)
#
#        gz = gz0 + gz00
#
#        return fx, fz, gx, gz
#
#
#    def cn_solver( self, x, z, Cur_vec, delta_t ) :
#        """
#        Crank-Nicholson solver for marching through time
#        """
#        Cur_prev, Cur, Cur_nxt = Cur_vec[0], Cur_vec[1], Cur_vec[2]
#
#        maxIters = 10
#        tol      = 1e-4
#
#        Nx = self.num_diff_vars #self.N+self.Na+self.Nc
#        Nz = self.num_algr_vars #self.Na + self.Nc + self.N + self.Na + self.Nc
#
#        x_nxt = numpy.zeros( (Nx,maxIters), dtype='d' )
#        z_nxt = numpy.zeros( (Nz,maxIters), dtype='d' )
#
#        relres = numpy.zeros( maxIters, dtype='d' )
#        relres[0] = 1.0
#
#        var_flag = {'lim_on':0}
#
#        # Solve for consistent ICs
#        if Cur != Cur_prev :    
#            z_cons = numpy.zeros( (Nz, maxIters), dtype='d' )
#            z_cons[:,0] = deepcopy(z)
#
#            junk_f, g, mats = self.dae_system( x, z, Cur, get_mats=1 )
#            for idx in range(maxIters-1) :
#                (junk_fx, junk_fz, junk_gx, g_z) = self.jac_system( mats )
#
#                Delta_z = -sparseSolve( sparseMat(g_z), g )
#                z_cons[:,idx+1] = z_cons[:,idx] + Delta_z
#
#                relres_z = numpy.linalg.norm(Delta_z,numpy.inf) / numpy.linalg.norm(z,numpy.inf)
#                if relres_z < tol :
#                    break
#                elif idx == maxIters-1 :
#                    print(('Warning: Max Newton iterations reached for consistency | RelChange=',relres_z*100.0))
#
#            z = z_cons[:,idx+1]
#
#        #print Cur
#
#        f, g = self.dae_system( deepcopy(x), deepcopy(z), Cur )
#
#        x_nxt[:,0] = deepcopy(x)
#        z_nxt[:,0] = deepcopy(z)
#        
#       # plt.figure(1)
#       # plt.plot( x_nxt[:,0] )
#       # plt.plot( z_nxt[:,0] )
#       # plt.show()
#
#        for idx in range(maxIters-1) :
#            f_nxt, g_nxt, mats = self.dae_system( x_nxt[:,idx], z_nxt[:,idx], Cur_nxt, get_mats=1  )
#
##            print 'x:',x.shape
##            print 'xnxt:',x_nxt[:,idx].shape
##            print 'f:',f.shape
##            print 'fnxt:',f_nxt.shape
#
##            print 'z:', z.shape
##            print 'g:', g.shape
##            print 'znxt:', z_nxt[:,idx].shape
##            print 'gnxt:', g_nxt.shape
#
#            F1 = x - x_nxt[:,idx] + delta_t/2.*( f+f_nxt )
#            F2 = g_nxt
#            F  = numpy.concatenate( (F1, F2), axis=0 )
#
#            fx, fz, gx, gz = self.jac_system( mats )
#
##            jmat = numpy.concatenate( (numpy.concatenate( (fx, fz), axis=1 ), 
##                                       numpy.concatenate( (gx, gz), axis=1 )) )
##
##            self.Input = Cur_nxt
##            jmat_num = compute_deriv( self.dae_system_num, numpy.concatenate( (x_nxt[:,idx], z_nxt[:,idx]) ) )
##
##            fx_num = jmat_num[:self.num_diff_vars,:self.num_diff_vars]
##            fz_num = jmat_num[:self.num_diff_vars,self.num_diff_vars:]
##            gx_num = jmat_num[self.num_diff_vars:,:self.num_diff_vars]
##            gz_num = jmat_num[self.num_diff_vars:,self.num_diff_vars:]
##
##            F1x_num = -sparse.eye(len(x)) + delta_t/2. * fx_num
##            F1z_num = delta_t/2. * fz_num
#
#            F1_x = -sparse.eye(len(x)) + delta_t/2. * fx
#            F1_z = delta_t/2. * fz
#            F2_x = gx
#            F2_z = gz
#
#            J = numpy.concatenate( (numpy.concatenate( (F1_x, F1_z), axis=1 ), 
#                                    numpy.concatenate( (F2_x, F2_z), axis=1 )) )
#
##            Jnum = numpy.concatenate( (numpy.concatenate( (F1x_num, F1z_num), axis=1 ), 
##                                       numpy.concatenate( (gx_num , gz_num ), axis=1 )) )
#
#            Jsp = sparseMat( J )
#
##            Jspnum = sparseMat( Jnum )
#
#
##            Delta_y = -sparseSolve( Jspnum, F )
#            Delta_y = -sparseSolve( Jsp, F )
#
#            x_nxt[:,idx+1] = x_nxt[:,idx] + Delta_y[:Nx]
#            z_nxt[:,idx+1] = z_nxt[:,idx] + Delta_y[Nx:]
#
#
#         #   plt.figure(1)
#          #  plt.plot(Delta_y)
#
#           # plt.figure(2)
#         #   plt.plot(x_nxt[:,idx])
#          #  plt.plot(x_nxt[:,idx+1])
#            
##            plt.show()
#
#            y = numpy.concatenate( (x_nxt[:,idx+1], z_nxt[:,idx+1]), axis=0 )
#            relres[idx+1] = numpy.linalg.norm( Delta_y, numpy.inf ) / numpy.linalg.norm( y, numpy.inf ) 
#
#            if (relres[idx+1]<tol) and (numpy.linalg.norm(F, numpy.inf)<tol) :
#                break
#            elif idx==maxIters-1 :
#                print( ('Warning: Max Newton iterations reached in main CN loop | RelChange = ',relres[-1]*100.0) )
#
#        x_nxtf = x_nxt[:,idx+1]
#        z_nxtf = z_nxt[:,idx+1]
#
#        newtonStats = {'var_flag':var_flag}
#        newtonStats['iters']    = idx
#        newtonStats['relres']   = relres
#
##        jm1_sp = sps.csr_matrix(jmat)
##        jm2_sp = sps.csr_matrix(jmat_num)
#
##        fig, ax = plt.subplots(1,2)
##        ax[0].spy( jm1_sp )
##        ax[1].spy( jm2_sp )
##        plt.show()
#
##        rtol_check = 0.1
##        print '###############################################'
##        print 'numpy.allclose( fx, fx_num, rtol=0.001 ):', numpy.allclose( fx, fx_num, rtol=rtol_check )
##        
##        print '###############################################'
##        print 'numpy.allclose( fz, fz_num, rtol=0.001 ):', numpy.allclose( fz, fz_num, rtol=rtol_check )
##
##        print '###############################################'
##        print 'numpy.allclose( gx, gx_num, rtol=0.001 ):', numpy.allclose( gx, gx_num, rtol=rtol_check )
##        
##        print '###############################################'
##        print 'numpy.allclose( gz, gz_num, rtol=0.001 ):', numpy.allclose( gz, gz_num, rtol=rtol_check )
##
##        print '###############################################'
##        print 'numpy.allclose( jmat, jmat_num, rtol=0.001 ):', numpy.allclose( jmat, jmat_num, rtol=rtol_check )
##
##        jm1_sp = sps.csr_matrix(jmat)
##        jm2_sp = sps.csr_matrix(jmat_num)
##
##        fig, ax = plt.subplots(1,2)
##        ax[0].spy( jm1_sp )
##        ax[0].set_title('Analytical Jacobian')
##        ax[1].spy( jm2_sp )
##        ax[1].set_title('Numerical Jacobian')
##        plt.suptitle( 'numpy.allclose( jmat, jmat_num, rtol=0.001 ):' + str(numpy.allclose( jmat, jmat_num, rtol=0.001 )) )
##        plt.show()
##
##        print 'Finished t_step'
#
#        return x_nxtf, z_nxtf, newtonStats
#
